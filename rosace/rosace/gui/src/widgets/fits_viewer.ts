/**
 * Generic, reusable FITS image viewer widget -- pan/zoom, plus an adjustable
 * visualization stretch (whole image or a user-selected region) computed via
 * 1%/99% percentile clipping, plus a toolbar readout of the pixel under the
 * cursor: X, Y and intensity.
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

// Minimum drag distance (canvas pixels) for a zone-mode pointer gesture to count
// as "drawing a rectangle" rather than "a plain click" (which clears the selection).
const SELECT_CLICK_THRESHOLD = 3;

const STRETCH_LOW_PERCENTILE = 0.01;
const STRETCH_HIGH_PERCENTILE = 0.99;

/** A selection rectangle in image space (top-down, same convention as the
 * rendered/displayed image -- (0,0) is the top-left corner on screen). */
interface SelectionRect {
	x0: number;
	y0: number;
	x1: number;
	y1: number;
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

	private dragging = false;
	private dragStartX = 0;
	private dragStartY = 0;
	private dragOriginX = 0;
	private dragOriginY = 0;

	// Zone-selection mode ("Zone" toggle button): while on, drag draws/redraws
	// the selection rectangle instead of panning; while off, plain pan/zoom.
	// The rectangle itself is kept across a toggle-off (so re-enabling "Zone"
	// shows it again exactly where it was) and across incoming new images of
	// the same panel (same frame size assumed) -- only "Ajuster les niveaux"
	// ever reads it, and only while selectionMode is on.
	private selectionMode = false;
	private selection: SelectionRect | null = null;
	private selecting = false;
	private selectMoved = false;
	private selectStartCanvasX = 0;
	private selectStartCanvasY = 0;

	// User-adjusted stretch (set by "Ajuster les niveaux"). Once set, it stays
	// applied to every subsequent image loaded into this viewer (a new pose of
	// the same target must not silently reset it) until adjusted again.
	private hasCustomStretch = false;
	private stretchMin = 0;
	private stretchMax = 0;

	// Static reference rectangle (e.g. the focus sub-plugin's search window),
	// set from outside via setReferenceWindowFraction() -- null means "don't
	// draw one". Unlike `selection` above, this is never drawn by the user
	// and always a square centered on the image, so only a single fraction
	// (of the image's own smaller dimension) is needed, not four coordinates.
	private referenceWindowFraction: number | null = null;

	constructor( props: FitsViewerProps = {} ) {
		super( props );
		this.addClass( "fits-viewer" );

		this.setContent( [
			new HBox({ cls: "fits-toolbar", content: [
				new Button({ cls: "fits-btn", label: "Zoom", click: () => this.zoomFit() }),
				new Button({ cls: "fits-btn", label: "Zoom H", click: () => this.zoomFitWidth() }),
				new Button({ cls: "fits-btn", label: "1:1", click: () => this.zoomOneToOne() }),
				this.zoneButton = new Button({ cls: "fits-btn", label: "Zone", click: () => this.toggleZoneMode() }),
				new Button({ cls: "fits-btn", label: "Ajuster les niveaux", click: () => this.adjustLevels() }),
				this.readout = new Label({ cls: "fits-readout", text: "" }),
			]}),
			this.canvas = new Canvas({ cls: "fits-canvas", flex: 1, clear: true, paint_cb: ctx => this.paint( ctx ) }),
		]);

		this.canvas.addDOMEvent( "wheel", ( ev: WheelEvent ) => this.onWheel( ev ) );
		this.canvas.addDOMEvent( "pointerdown", ( ev: PointerEvent ) => this.onPointerDown( ev ) );
		this.canvas.addDOMEvent( "pointermove", ( ev: PointerEvent ) => this.onPointerMove( ev ) );
		this.canvas.addDOMEvent( "pointerup", ( ev: PointerEvent ) => this.onPointerUp( ev ) );
		this.canvas.addDOMEvent( "pointerleave", () => this.setReadout( "" ) );
		this.canvas.addDOMEvent( "contextmenu", ( ev: MouseEvent ) => this.onContextMenu( ev ) );
	}

	/**
	 * Parses raw FITS bytes and displays them, fit to the current canvas size.
	 * Preserves any previously-set custom stretch (hasCustomStretch) and the
	 * current selection rectangle -- neither resets just because a new pose
	 * of the same target came in.
	 */

	loadFits( buffer: ArrayBuffer ) {
		this.image = parseFits( buffer );
		this.setReadout( "" );	// a value read from the previous image must not linger
		const [ min, max ] = this.hasCustomStretch
			? [ this.stretchMin, this.stretchMax ]
			: [ this.image.min, this.image.max ];
		this.offscreen = renderToOffscreenCanvas( this.image, min, max );
		this.zoomFit();
	}

	/**
	 * Recomputes the visualization stretch (1%/99% percentile clipping) from
	 * the current selection rectangle if zone mode is on and a rectangle has
	 * been drawn, or from the whole image otherwise -- and applies it. Stays
	 * in effect for every later image loaded into this viewer until called
	 * again.
	 */

	adjustLevels() {
		if( !this.image ) {
			return;
		}

		const region = ( this.selectionMode && this.selection ) ? this.selection : null;
		const [ min, max ] = computePercentileRange( this.image, region, STRETCH_LOW_PERCENTILE, STRETCH_HIGH_PERCENTILE );

		this.hasCustomStretch = true;
		this.stretchMin = min;
		this.stretchMax = max;

		this.offscreen = renderToOffscreenCanvas( this.image, min, max );
		this.canvas.redraw();
	}

	/**
	 * Sets (or clears, with null) a static square reference rectangle, centered
	 * on the image, whose side is `fraction` times the image's own smaller
	 * dimension -- e.g. the focus sub-plugin's own search window. Unlike the
	 * "Zone" selection above, this is never drawn by the user and has nothing
	 * to do with the visualization stretch; it's purely an informational
	 * overlay, recomputed from the currently-loaded image's own dimensions on
	 * every paint(), never stored in image-space coordinates.
	 */

	setReferenceWindowFraction( fraction: number | null ) {
		this.referenceWindowFraction = fraction;
		this.canvas.redraw();
	}

	/**
	 * Toggles zone-selection mode. The rectangle itself (if any) is kept
	 * across the toggle -- only the ability to draw/use it turns on or off.
	 */

	private toggleZoneMode() {
		this.selectionMode = !this.selectionMode;
		this.zoneButton.setClass( "active", this.selectionMode );
		this.canvas.setClass( "zone-mode", this.selectionMode );
		this.canvas.redraw();
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

		if( this.selectionMode ) {
			const rc = this.canvas.getBoundingRect();
			this.selecting = true;
			this.selectMoved = false;
			this.selectStartCanvasX = ev.pageX - rc.left;
			this.selectStartCanvasY = ev.pageY - rc.top;
			this.canvas.setCapture( ev.pointerId );
			return;
		}

		this.dragging = true;
		this.canvas.setCapture( ev.pointerId );
		this.dragStartX = ev.pageX;
		this.dragStartY = ev.pageY;
		this.dragOriginX = this.offsetX;
		this.dragOriginY = this.offsetY;
		this.canvas.setClass( "v-pan", ev.ctrlKey );
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

			const p0 = this.toImageSpace( this.selectStartCanvasX, this.selectStartCanvasY );
			const p1 = this.toImageSpace( cx, cy );
			this.selection = { x0: p0.x, y0: p0.y, x1: p1.x, y1: p1.y };
			this.canvas.redraw();
			this.updateReadout( ev );	// the image doesn't move while selecting a zone
			return;
		}

		if( !this.dragging ) {
			this.updateReadout( ev );
			return;
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

			// a plain click (no real drag) clears the current selection instead
			// of leaving a zero-size rectangle behind
			if( !this.selectMoved ) {
				this.selection = null;
				this.canvas.redraw();
			}
			return;
		}

		if( !this.dragging ) {
			return;
		}

		this.dragging = false;
		this.canvas.releaseCapture( ev.pointerId );
		this.canvas.setClass( "v-pan", false );
	}

	/**
	 * Right-click: a small help/actions menu, replacing the browser's own
	 * (heterogeneous, extension-polluted) default context menu with something
	 * actually relevant to this viewer -- mirrors the toolbar buttons plus a
	 * few lines documenting the mouse/keyboard gestures that have no button.
	 */

	private onContextMenu( ev: MouseEvent ) {
		if( !this.image ) {
			return;
		}

		const menu = new Menu({ items: [
			{ text: "Zoom", click: () => this.zoomFit() },
			{ text: "Zoom H", click: () => this.zoomFitWidth() },
			{ text: "1:1", click: () => this.zoomOneToOne() },
			{ text: this.selectionMode ? "Désactiver Zone" : "Activer Zone", click: () => this.toggleZoneMode() },
			{ text: "Ajuster les niveaux", click: () => this.adjustLevels() },
			"-",
			"Molette : zoom",
			"Glisser : déplacer l'image",
			"Ctrl + glisser : déplacer verticalement seulement",
			"Zone + glisser : sélectionner une zone",
		]});

		menu.displayAt( ev.pageX, ev.pageY );
		ev.stopPropagation();
		ev.preventDefault();
	}

	private paint( ctx: CanvasEx ) {
		if( !this.image || !this.offscreen ) {
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

		if( this.selectionMode && this.selection ) {
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

		if( this.referenceWindowFraction ) {
			// Centered square, same computation as focus.py::_central_window() --
			// symmetric, so image-space top-down vs. bottom-up doesn't matter here.
			const side = Math.min( this.image.width, this.image.height ) * this.referenceWindowFraction;
			const x0 = this.offsetX + ( this.image.width  - side ) / 2 * this.scale;
			const y0 = this.offsetY + ( this.image.height - side ) / 2 * this.scale;
			const s = side * this.scale;

			ctx.save();
			ctx.strokeStyle = "#00e5ff";
			ctx.lineWidth = 1;
			ctx.strokeRect( x0, y0, s, s );
			ctx.restore();
		}
	}
}

/**
 * Renders a FITS image to an offscreen 8-bit canvas via a linear stretch
 * between the given `min`/`max` bounds -- the caller decides those bounds
 * (the image's own true min/max by default, or a percentile-clipped range
 * from "Ajuster les niveaux", see FitsViewer.adjustLevels()).
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
 * null -- the visualization stretch computed by "Ajuster les niveaux",
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
