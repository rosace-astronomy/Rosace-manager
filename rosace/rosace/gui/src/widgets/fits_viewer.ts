/**
 * Generic, reusable FITS image viewer widget -- pan/zoom, plus an adjustable
 * visualization stretch (whole image or a user-selected region) computed via
 * 1%/99% percentile clipping, plus a toolbar readout of the pixel under the
 * cursor: X, Y and intensity.
 *
 * Mouse: left drag draws the zone (yellow rectangle), double-click releases
 * it; right drag pans (Ctrl: vertically only), a right click without moving
 * opens the actions menu (zoom on the zone, stretch); the wheel zooms. Same
 * gestures as SpectrumViewer. HELP_LINES lists them for the "Aide" button.
 *
 * Pixel coordinates follow Rosace's app-wide convention (see CLAUDE.md,
 * "Pixel coordinate convention"): 0-indexed, origin at the centre of the
 * bottom-left pixel, X to the right, Y upwards (FITS row order) -- the same as
 * astropy's WCS and the slit_x/slit_y config values. KStars/DS9 count from 1.
 */

import { VBox, HBox, Button, Canvas, CanvasEx, BoxProps, Label, Menu } from "x4js";

import { parseFits, FitsImage } from "./fits_parser";

const MIN_SCALE = 0.05;
const MAX_SCALE = 50;
const WHEEL_ZOOM_FACTOR = 1.15;

// Minimum drag distance (canvas pixels) for a pointer gesture to count
// as a drag rather than a plain click (a plain right click opens the menu).
const SELECT_CLICK_THRESHOLD = 3;

const STRETCH_LOW_PERCENTILE = 0.01;
const STRETCH_HIGH_PERCENTILE = 0.99;

const LEFT_BUTTON = 0;
const RIGHT_BUTTON = 2;

const HELP_LINES = [
	"Clic gauche + glisser : tracer une zone",
	"Double-clic : libérer la zone",
	"Clic droit + glisser : déplacer l'image",
	"Ctrl + clic droit + glisser : déplacer verticalement",
	"Clic droit : menu (agrandir la zone, seuils)",
	"Molette : zoom",
	"Bouton Zone (jaune) : libère la zone",
];

/** A selection rectangle in image space (top-down, same convention as the
 * rendered/displayed image -- (0,0) is the top-left corner on screen). */
interface SelectionRect {
	x0: number;
	y0: number;
	x1: number;
	y1: number;
}

/**
 * Overlay sent by a sub-plugin with its image (see image_broadcast.py), in
 * Rosace's pixel convention: window and zone = [x0, y0, x1, y1] outer pixel
 * edges (solid / dashed rectangle), line = [xa, ya, xb, yb], point = [x, y].
 * Every element is optional; the viewer draws whatever is present, without
 * knowing which sub-plugin sent it.
 */
export interface ImageOverlay {
	window?: [ number, number, number, number ] | null;
	zone?: [ number, number, number, number ] | null;
	line?: [ number, number, number, number ] | null;
	point?: [ number, number ] | null;
}

export interface FitsViewerProps extends BoxProps {
}

export class FitsViewer extends VBox {

	private canvas: Canvas;
	private zoneButton: Button;
	private readout: Label;
	private lastReadout = "";
	private offscreen: HTMLCanvasElement;
	private image: FitsImage;

	private scale = 1;
	private offsetX = 0;
	private offsetY = 0;

	// Right-button drag: pans; a right click without moving opens the menu
	private dragging = false;
	private dragMoved = false;
	private dragStartX = 0;
	private dragStartY = 0;
	private dragOriginX = 0;
	private dragOriginY = 0;

	// The zone (left-button drag): kept until released (double-click or the
	// "Zone" button) and across new images of the same size; used by "Seuils"
	// and the menu's zoom-on-zone actions. A plain left click leaves it as is.
	private selection: SelectionRect | null = null;
	private selecting = false;
	private selectMoved = false;
	private selectStartCanvasX = 0;
	private selectStartCanvasY = 0;

	// Current stretch: recomputed on the whole image for the first image and
	// whenever the type of image changes (kind, sent by the server: science,
	// dark, flat, guide, slit...), or by "Seuils"; otherwise kept from one
	// image to the next (a new pose of the same kind keeps the user's choice).
	private stretchMin = 0;
	private stretchMax = 0;
	private kind: string | undefined = undefined;

	// Per-image overlay sent with the image by the sub-plugin that produced it,
	// set from outside via setOverlay() -- null means "don't draw one".
	private overlay: ImageOverlay | null = null;

	constructor( props: FitsViewerProps = {} ) {
		super( props );
		this.addClass( "fits-viewer" );

		this.setContent( [
			new HBox({ cls: "fits-toolbar", content: [
				new Button({ cls: "fits-btn", label: "Tout", click: () => this.zoomFit() }),
				new Button({ cls: "fits-btn", label: "Zoom H", click: () => this.zoomFitWidth() }),
				new Button({ cls: "fits-btn", label: "1:1", click: () => this.zoomOneToOne() }),
				this.zoneButton = new Button({ cls: "fits-btn", label: "Zone", tooltip: "Jaune : une zone est tracée ; clic : la libérer", click: () => this.clearZone() }),
				new Button({ cls: "fits-btn", label: "Seuils", tooltip: "Seuils d'affichage (1 %/99 %) sur la zone, ou sur toute l'image", click: () => this.adjustLevels() }),
				new Button({ cls: "fits-btn", label: "Aide", click: () => this.showHelp() }),
				this.readout = new Label({ cls: "fits-readout", text: "" }),
			]}),
			this.canvas = new Canvas({ cls: "fits-canvas", flex: 1, clear: true, paint_cb: ctx => this.paint( ctx ) }),
		]);

		this.canvas.addDOMEvent( "wheel", ( ev: WheelEvent ) => this.onWheel( ev ) );
		this.canvas.addDOMEvent( "pointerdown", ( ev: PointerEvent ) => this.onPointerDown( ev ) );
		this.canvas.addDOMEvent( "pointermove", ( ev: PointerEvent ) => this.onPointerMove( ev ) );
		this.canvas.addDOMEvent( "pointerup", ( ev: PointerEvent ) => this.onPointerUp( ev ) );
		this.canvas.addDOMEvent( "pointerleave", () => this.setReadout( "" ) );
		// the menu opens on a right click without moving (onPointerUp); the
		// browser's own context menu never shows
		this.canvas.addDOMEvent( "contextmenu", ( ev: MouseEvent ) => ev.preventDefault() );
		this.canvas.addDOMEvent( "dblclick", () => this.clearZone() );
	}

	/**
	 * Parses raw FITS bytes and displays them. kind is the type of image sent
	 * by the server ("science", "dark", "flat", "calib", "bias", "guide",
	 * "slit"; undefined for the last image reloaded at startup).
	 * - Stretch: recomputed on the whole image for the first image and when
	 *   the kind changes, kept otherwise (see stretchMin/stretchMax).
	 * - Zoom and zone: kept when the new image has the same size as the
	 *   previous one; otherwise the image is fit and the zone released.
	 */

	loadFits( buffer: ArrayBuffer, kind?: string ) {
		const previous = this.image;
		this.image = parseFits( buffer );
		this.setReadout( "" );	// a value read from the previous image must not linger

		if( !previous || kind !== this.kind ) {
			[ this.stretchMin, this.stretchMax ] = computePercentileRange(
				this.image, null, STRETCH_LOW_PERCENTILE, STRETCH_HIGH_PERCENTILE );
		}
		this.kind = kind;
		this.offscreen = renderToOffscreenCanvas( this.image, this.stretchMin, this.stretchMax );

		const sameSize = previous && previous.width === this.image.width && previous.height === this.image.height;
		if( sameSize ) {
			this.canvas.redraw();
		}
		else {
			this.setSelection( null );
			this.zoomFit();
		}
	}

	/**
	 * "Seuils": recomputes the visualization stretch (1%/99% percentile
	 * clipping) on the zone if one is drawn, on the whole image otherwise --
	 * and applies it. Kept for the next images of the same kind.
	 */

	adjustLevels() {
		if( !this.image ) {
			return;
		}

		[ this.stretchMin, this.stretchMax ] = computePercentileRange(
			this.image, this.selection, STRETCH_LOW_PERCENTILE, STRETCH_HIGH_PERCENTILE );
		this.offscreen = renderToOffscreenCanvas( this.image, this.stretchMin, this.stretchMax );
		this.canvas.redraw();
	}

	/**
	 * Sets (or clears, with null) the image's overlay. Its coordinates are in
	 * Rosace's pixel convention (0-indexed, pixel centres on integers, Y
	 * upwards -- see toCanvas()), sent by the backend with the image itself.
	 */

	setOverlay( overlay: ImageOverlay | null ) {
		this.overlay = overlay;
		this.canvas.redraw();
	}

	/**
	 * Converts a point in Rosace's pixel convention (0-indexed, pixel centre on
	 * integers, Y upwards -- FITS row 0 is the bottom row) to canvas coordinates:
	 * the image is displayed top-down, so row y is drawn at height - 1 - y.
	 */

	private toCanvas( x: number, y: number ): { x: number, y: number } {
		return {
			x: this.offsetX + ( x + 0.5 ) * this.scale,
			y: this.offsetY + ( this.image.height - y - 0.5 ) * this.scale,
		};
	}

	/** Sets (or releases, with null) the zone; the "Zone" button is yellow while one exists. */

	private setSelection( selection: SelectionRect | null ) {
		this.selection = selection;
		this.zoneButton.setClass( "active", selection !== null );
	}

	/** Releases the zone (double-click, "Zone" button); the stretch stays as it is. */

	private clearZone() {
		this.setSelection( null );
		this.canvas.redraw();
	}

	/**
	 * Zooms so the zone fills the panel ("Agrandir zone"), or so its width
	 * fills it ("Agrandir zone horizontale"), centred on the zone.
	 */

	private zoomZone( widthOnly: boolean ) {
		if( !this.image || !this.selection ) {
			return;
		}
		const rc = this.canvas.getBoundingRect();
		const x0 = Math.min( this.selection.x0, this.selection.x1 );
		const x1 = Math.max( this.selection.x0, this.selection.x1 );
		const y0 = Math.min( this.selection.y0, this.selection.y1 );
		const y1 = Math.max( this.selection.y0, this.selection.y1 );
		const w = Math.max( x1 - x0, 1 );
		const h = Math.max( y1 - y0, 1 );

		const scale = widthOnly ? rc.width / w : Math.min( rc.width / w, rc.height / h );
		this.scale = Math.min( MAX_SCALE, Math.max( MIN_SCALE, scale ) );
		this.offsetX = rc.width / 2 - ( x0 + x1 ) / 2 * this.scale;
		this.offsetY = rc.height / 2 - ( y0 + y1 ) / 2 * this.scale;
		this.canvas.redraw();
	}

	/** "Aide" button: the mouse gestures, in the middle of the image. */

	private showHelp() {
		new Menu({ items: HELP_LINES }).displayNear( this.canvas.getBoundingRect(), "center middle", "center middle" );
	}

	/** Header cards of the displayed image (OBJECT, OBS_ID... for the panel title), null without one. */

	getHeader(): Record<string, string> | null {
		return this.image ? this.image.header : null;
	}

	/**
	 * Scales the image so it fits entirely within the canvas ("zoom plein cadre").
	 */

	zoomFit() {
		if( !this.image ) {
			return;
		}

		const rc = this.canvas.getBoundingRect();
		if( !rc.width || !rc.height ) {
			return;
		}

		this.scale = Math.min( rc.width / this.image.width, rc.height / this.image.height );
		this.centerImage( rc.width, rc.height );
		this.canvas.redraw();
	}

	/**
	 * Scales the image so its width fills the canvas exactly ("Zoom H") --
	 * the height may then exceed the canvas (cropped top/bottom), which is
	 * fine for panels where the full height is rarely needed. Deliberately
	 * does NOT re-center vertically: keeps whatever image-space Y coordinate
	 * is currently at the canvas's vertical center still there after the
	 * rescale, so an existing vertical pan position survives a "Zoom H".
	 */

	zoomFitWidth() {
		if( !this.image ) {
			return;
		}

		const rc = this.canvas.getBoundingRect();
		if( !rc.width ) {
			return;
		}

		const centerY = ( rc.height / 2 - this.offsetY ) / this.scale;

		this.scale = rc.width / this.image.width;
		this.offsetX = ( rc.width - this.image.width * this.scale ) / 2;
		this.offsetY = rc.height / 2 - centerY * this.scale;

		this.canvas.redraw();
	}

	/**
	 * Sets the scale to exactly one screen pixel per image pixel.
	 */

	zoomOneToOne() {
		if( !this.image ) {
			return;
		}

		const rc = this.canvas.getBoundingRect();
		this.scale = 1;
		this.centerImage( rc.width, rc.height );
		this.canvas.redraw();
	}

	private centerImage( canvasWidth: number, canvasHeight: number ) {
		this.offsetX = ( canvasWidth  - this.image.width  * this.scale ) / 2;
		this.offsetY = ( canvasHeight - this.image.height * this.scale ) / 2;
	}

	/** Canvas-pixel to image-space (top-down, matching the rendered/displayed image). */
	private toImageSpace( canvasX: number, canvasY: number ): { x: number, y: number } {
		return {
			x: ( canvasX - this.offsetX ) / this.scale,
			y: ( canvasY - this.offsetY ) / this.scale,
		};
	}

	private onWheel( ev: WheelEvent ) {
		if( !this.image ) {
			return;
		}
		ev.preventDefault();

		const rc = this.canvas.getBoundingRect();
		const mx = ev.pageX - rc.left;
		const my = ev.pageY - rc.top;

		// image-space coordinate currently under the cursor, kept fixed across the zoom
		const { x: ix, y: iy } = this.toImageSpace( mx, my );

		const factor = ev.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
		this.scale = Math.min( MAX_SCALE, Math.max( MIN_SCALE, this.scale * factor ) );

		this.offsetX = mx - ix * this.scale;
		this.offsetY = my - iy * this.scale;

		this.canvas.redraw();
		this.updateReadout( ev );	// the image moved under a still cursor
	}

	/**
	 * Shows X, Y and intensity of the pixel under the cursor (Rosace's pixel
	 * convention, see the file header), or clears the readout when there is no
	 * image or the cursor is outside it.
	 */

	private updateReadout( ev: MouseEvent ) {
		if( !this.image ) {
			this.setReadout( "" );
			return;
		}

		const rc = this.canvas.getBoundingRect();
		const p = this.toImageSpace( ev.pageX - rc.left, ev.pageY - rc.top );
		const col = Math.floor( p.x );
		const row = Math.floor( p.y );	// top-down, as displayed

		const { width, height, data } = this.image;
		if( col < 0 || col >= width || row < 0 || row >= height ) {
			this.setReadout( "" );
			return;
		}

		// FITS row 0 is the bottom row, and Y counts upwards: the displayed
		// (top-down) row is flipped, exactly like renderToOffscreenCanvas() does
		const y = height - 1 - row;
		const v = data[y * width + col];
		this.setReadout( `X: ${col}  Y: ${y}  I: ${Number.isInteger( v ) ? v : v.toFixed( 2 )}` );
	}

	/** Sets the readout text, skipping the DOM update when unchanged (pointermove fires at a high rate). */

	private setReadout( text: string ) {
		if( text !== this.lastReadout ) {
			this.lastReadout = text;
			this.readout.setText( text );
		}
	}

	private onPointerDown( ev: PointerEvent ) {
		if( !this.image ) {
			return;
		}

		if( ev.button === LEFT_BUTTON ) {
			const rc = this.canvas.getBoundingRect();
			this.selecting = true;
			this.selectMoved = false;
			this.selectStartCanvasX = ev.pageX - rc.left;
			this.selectStartCanvasY = ev.pageY - rc.top;
			this.canvas.setCapture( ev.pointerId );
			this.canvas.setClass( "zone-mode", true );
			return;
		}

		if( ev.button === RIGHT_BUTTON ) {
			this.dragging = true;
			this.dragMoved = false;
			this.canvas.setCapture( ev.pointerId );
			this.canvas.setClass( "panning", true );
			this.dragStartX = ev.pageX;
			this.dragStartY = ev.pageY;
			this.dragOriginX = this.offsetX;
			this.dragOriginY = this.offsetY;
		}
	}

	private onPointerMove( ev: PointerEvent ) {
		// The readout is refreshed in each branch below, always AFTER whatever
		// moves the image: during a pan the image follows the cursor, so the
		// pixel under it must be computed with the new offsets (a plain pan then
		// leaves it unchanged), not the ones from before this event.

		if( this.selecting ) {
			const rc = this.canvas.getBoundingRect();
			const cx = ev.pageX - rc.left;
			const cy = ev.pageY - rc.top;

			if( Math.abs( cx - this.selectStartCanvasX ) > SELECT_CLICK_THRESHOLD
				|| Math.abs( cy - this.selectStartCanvasY ) > SELECT_CLICK_THRESHOLD ) {
				this.selectMoved = true;
			}

			// a plain click (no real drag yet) leaves the current zone as it is
			if( this.selectMoved ) {
				const p0 = this.toImageSpace( this.selectStartCanvasX, this.selectStartCanvasY );
				const p1 = this.toImageSpace( cx, cy );
				this.setSelection( { x0: p0.x, y0: p0.y, x1: p1.x, y1: p1.y } );
				this.canvas.redraw();
			}
			this.updateReadout( ev );	// the image doesn't move while selecting a zone
			return;
		}

		if( !this.dragging ) {
			this.updateReadout( ev );
			return;
		}

		if( Math.abs( ev.pageX - this.dragStartX ) > SELECT_CLICK_THRESHOLD
			|| Math.abs( ev.pageY - this.dragStartY ) > SELECT_CLICK_THRESHOLD ) {
			this.dragMoved = true;
		}

		// Ctrl held: constrain the drag to vertical movement only (handy in
		// "Zoom H", where the width is already exactly fit and any horizontal
		// drift is unwanted) -- without Ctrl, unchanged free pan.
		if( !ev.ctrlKey ) {
			this.offsetX = this.dragOriginX + ( ev.pageX - this.dragStartX );
		}
		this.offsetY = this.dragOriginY + ( ev.pageY - this.dragStartY );
		this.canvas.setClass( "v-pan", ev.ctrlKey );
		this.canvas.redraw();
		this.updateReadout( ev );
	}

	private onPointerUp( ev: PointerEvent ) {
		if( this.selecting ) {
			this.selecting = false;
			this.canvas.releaseCapture( ev.pointerId );
			this.canvas.setClass( "zone-mode", false );
			return;
		}

		if( !this.dragging ) {
			return;
		}

		this.dragging = false;
		this.canvas.releaseCapture( ev.pointerId );
		this.canvas.setClass( "panning", false );
		this.canvas.setClass( "v-pan", false );
		if( !this.dragMoved ) {
			this.showMenu( ev );
		}
	}

	/** Right click without moving: the actions menu. */

	private showMenu( ev: MouseEvent ) {
		const noZone = this.selection === null;
		new Menu({ items: [
			{ text: "Agrandir zone", disabled: noZone, click: () => this.zoomZone( false ) },
			{ text: "Agrandir zone horizontale", disabled: noZone, click: () => this.zoomZone( true ) },
			{ text: "Seuils", click: () => this.adjustLevels() },
			"-",
			{ text: "Tout", click: () => this.zoomFit() },
			{ text: "Zoom H", click: () => this.zoomFitWidth() },
			{ text: "1:1", click: () => this.zoomOneToOne() },
		]}).displayAt( ev.pageX, ev.pageY );
	}

	private paint( ctx: CanvasEx ) {
		if( !this.image || !this.offscreen ) {
			// same empty state as SpectrumViewer's "Aucun spectre"
			ctx.font = "11px sans-serif";
			ctx.fillStyle = getComputedStyle( this.dom ).getPropertyValue( "--muted" ).trim() || "#7c7364";
			ctx.textAlign = "center";
			ctx.fillText( "Aucune image", ctx.width / 2, ctx.height / 2 );
			return;
		}

		// nearest-neighbour when zoomed in (keeps individual pixels sharp/measurable),
		// smoothed when zoomed out (avoids a noisy, aliased thumbnail)
		ctx.imageSmoothingEnabled = this.scale < 1;

		ctx.drawImage(
			this.offscreen,
			0, 0, this.image.width, this.image.height,
			this.offsetX, this.offsetY, this.image.width * this.scale, this.image.height * this.scale,
		);

		if( this.selection ) {
			const x0 = this.offsetX + Math.min( this.selection.x0, this.selection.x1 ) * this.scale;
			const y0 = this.offsetY + Math.min( this.selection.y0, this.selection.y1 ) * this.scale;
			const w = Math.abs( this.selection.x1 - this.selection.x0 ) * this.scale;
			const h = Math.abs( this.selection.y1 - this.selection.y0 ) * this.scale;

			ctx.save();
			ctx.strokeStyle = "#ffcc00";
			ctx.lineWidth = 1;
			ctx.setLineDash( [ 5, 4 ] );
			ctx.strokeRect( x0, y0, w, h );
			ctx.restore();
		}

		if( this.overlay ) {
			this.paintOverlay( ctx, this.overlay );
		}
	}

	/**
	 * Image overlay: window (solid cyan rectangle), zone (dashed magenta
	 * rectangle), line (solid red) and point (green cross) -- each colour
	 * distinct from the yellow "Zone" selection drawn by the user.
	 */

	private paintOverlay( ctx: CanvasEx, overlay: ImageOverlay ) {
		ctx.save();
		ctx.lineWidth = 1;

		if( overlay.window ) {
			// Outer pixel edges, mapped like zone below; y1 is the top
			const [ x0, y0, x1, y1 ] = overlay.window;
			const a = this.toCanvas( x0, y1 );
			const b = this.toCanvas( x1, y0 );
			ctx.strokeStyle = "#00e5ff";
			ctx.strokeRect( a.x, a.y, b.x - a.x, b.y - a.y );
		}

		if( overlay.zone ) {
			const [ x0, y0, x1, y1 ] = overlay.zone;
			// zone values are pixel edges (e.g. -0.5 = left edge of column 0), which
			// toCanvas() maps exactly onto the drawn pixel boundaries; y1 is the top
			const a = this.toCanvas( x0, y1 );
			const b = this.toCanvas( x1, y0 );
			ctx.strokeStyle = "#ff66ff";
			ctx.setLineDash( [ 6, 4 ] );
			ctx.strokeRect( a.x, a.y, b.x - a.x, b.y - a.y );
			ctx.setLineDash( [] );
		}

		if( overlay.line ) {
			const [ xa, ya, xb, yb ] = overlay.line;
			const a = this.toCanvas( xa, ya );
			const b = this.toCanvas( xb, yb );
			ctx.strokeStyle = "#ff3b30";
			ctx.beginPath();
			ctx.moveTo( a.x, a.y );
			ctx.lineTo( b.x, b.y );
			ctx.stroke();
		}

		if( overlay.point ) {
			const p = this.toCanvas( overlay.point[0], overlay.point[1] );
			const arm = 8;
			ctx.strokeStyle = "#39ff14";
			ctx.lineWidth = 2;
			ctx.beginPath();
			ctx.moveTo( p.x - arm, p.y );
			ctx.lineTo( p.x + arm, p.y );
			ctx.moveTo( p.x, p.y - arm );
			ctx.lineTo( p.x, p.y + arm );
			ctx.stroke();
		}

		ctx.restore();
	}
}

/**
 * Renders a FITS image to an offscreen 8-bit canvas via a linear stretch
 * between the given `min`/`max` bounds -- the caller decides those bounds
 * (a percentile-clipped range, see FitsViewer.loadFits() and adjustLevels()).
 */

function renderToOffscreenCanvas( image: FitsImage, min: number, max: number ): HTMLCanvasElement {
	const { width, height, data } = image;

	const canvas = document.createElement( "canvas" );
	canvas.width = width;
	canvas.height = height;

	const ctx = canvas.getContext( "2d" );
	const imgData = ctx.createImageData( width, height );
	const range = ( max - min ) || 1;

	// FITS row 0 is the bottom row (Y increases upward); canvas row 0 is the top,
	// so rows are flipped vertically while copying.
	for( let y = 0; y < height; y++ ) {
		const srcRow = height - 1 - y;
		for( let x = 0; x < width; x++ ) {
			const v = data[srcRow * width + x];
			const g = Math.max( 0, Math.min( 255, Math.round( ( v - min ) / range * 255 ) ) );
			const di = ( y * width + x ) * 4;
			imgData.data[di]     = g;
			imgData.data[di + 1] = g;
			imgData.data[di + 2] = g;
			imgData.data[di + 3] = 255;
		}
	}

	ctx.putImageData( imgData, 0, 0 );
	return canvas;
}

/**
 * Pixel values of `image` restricted to `region` (image space, top-down --
 * same convention as renderToOffscreenCanvas's output), or of the whole image
 * if `region` is null. Clamps the region to the image bounds.
 */

function collectValues( image: FitsImage, region: SelectionRect | null ): ArrayLike<number> {
	const { width, height, data } = image;
	if( !region ) {
		return data;
	}

	const x0 = Math.max( 0, Math.min( width  - 1, Math.round( Math.min( region.x0, region.x1 ) ) ) );
	const x1 = Math.max( 0, Math.min( width  - 1, Math.round( Math.max( region.x0, region.x1 ) ) ) );
	const y0 = Math.max( 0, Math.min( height - 1, Math.round( Math.min( region.y0, region.y1 ) ) ) );
	const y1 = Math.max( 0, Math.min( height - 1, Math.round( Math.max( region.y0, region.y1 ) ) ) );

	const out = new Float32Array( Math.max( 0, x1 - x0 + 1 ) * Math.max( 0, y1 - y0 + 1 ) );
	let i = 0;
	for( let y = y0; y <= y1; y++ ) {
		const srcRow = height - 1 - y;
		for( let x = x0; x <= x1; x++ ) {
			out[i++] = data[srcRow * width + x];
		}
	}
	return out;
}

/**
 * [min, max] at the `loPct`/`hiPct` percentiles (0-1) of the pixel values in
 * `region` (image space, top-down), or of the whole image if `region` is
 * null -- the visualization stretch computed by "Seuils",
 * robust to a single hot/saturated pixel unlike a bare min/max scan (see
 * [[project_gui_fits_viewer_stretch]]). Falls back to the image's own true
 * min/max if the region is degenerate (empty, or every pixel identical).
 */

function computePercentileRange(
	image: FitsImage, region: SelectionRect | null, loPct: number, hiPct: number
): [ number, number ] {
	const values = Array.from( collectValues( image, region ) ).sort( ( a, b ) => a - b );
	if( values.length === 0 ) {
		return [ image.min, image.max ];
	}

	const lo = values[ Math.floor( loPct * ( values.length - 1 ) ) ];
	const hi = values[ Math.floor( hiPct * ( values.length - 1 ) ) ];
	return lo < hi ? [ lo, hi ] : [ image.min, image.max ];
}
