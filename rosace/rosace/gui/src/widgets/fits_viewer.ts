/**
 * Generic, reusable FITS image viewer widget -- pan/zoom only for now (no threshold
 * control, no pixel readout yet, both deferred to a later iteration).
 */

import { VBox, HBox, Button, Canvas, CanvasEx, BoxProps } from "x4js";

import { parseFits, FitsImage } from "./fits_parser";

const MIN_SCALE = 0.05;
const MAX_SCALE = 50;
const WHEEL_ZOOM_FACTOR = 1.15;

export interface FitsViewerProps extends BoxProps {
}

export class FitsViewer extends VBox {

	private canvas: Canvas;
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

	constructor( props: FitsViewerProps = {} ) {
		super( props );
		this.addClass( "fits-viewer" );

		this.setContent( [
			new HBox({ cls: "fits-toolbar", content: [
				new Button({ cls: "fits-btn", label: "Ajuster", click: () => this.zoomFit() }),
				new Button({ cls: "fits-btn", label: "1:1", click: () => this.zoomOneToOne() }),
			]}),
			this.canvas = new Canvas({ cls: "fits-canvas", flex: 1, clear: true, paint_cb: ctx => this.paint( ctx ) }),
		]);

		this.canvas.addDOMEvent( "wheel", ( ev: WheelEvent ) => this.onWheel( ev ) );
		this.canvas.addDOMEvent( "pointerdown", ( ev: PointerEvent ) => this.onPointerDown( ev ) );
		this.canvas.addDOMEvent( "pointermove", ( ev: PointerEvent ) => this.onPointerMove( ev ) );
		this.canvas.addDOMEvent( "pointerup", ( ev: PointerEvent ) => this.onPointerUp( ev ) );
	}

	/**
	 * Parses raw FITS bytes and displays them, fit to the current canvas size.
	 */

	loadFits( buffer: ArrayBuffer ) {
		this.image = parseFits( buffer );
		this.offscreen = renderToOffscreenCanvas( this.image );
		this.zoomFit();
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

	private onWheel( ev: WheelEvent ) {
		if( !this.image ) {
			return;
		}
		ev.preventDefault();

		const rc = this.canvas.getBoundingRect();
		const mx = ev.pageX - rc.left;
		const my = ev.pageY - rc.top;

		// image-space coordinate currently under the cursor, kept fixed across the zoom
		const ix = ( mx - this.offsetX ) / this.scale;
		const iy = ( my - this.offsetY ) / this.scale;

		const factor = ev.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
		this.scale = Math.min( MAX_SCALE, Math.max( MIN_SCALE, this.scale * factor ) );

		this.offsetX = mx - ix * this.scale;
		this.offsetY = my - iy * this.scale;

		this.canvas.redraw();
	}

	private onPointerDown( ev: PointerEvent ) {
		if( !this.image ) {
			return;
		}

		this.dragging = true;
		this.canvas.setCapture( ev.pointerId );
		this.dragStartX = ev.pageX;
		this.dragStartY = ev.pageY;
		this.dragOriginX = this.offsetX;
		this.dragOriginY = this.offsetY;
	}

	private onPointerMove( ev: PointerEvent ) {
		if( !this.dragging ) {
			return;
		}

		this.offsetX = this.dragOriginX + ( ev.pageX - this.dragStartX );
		this.offsetY = this.dragOriginY + ( ev.pageY - this.dragStartY );
		this.canvas.redraw();
	}

	private onPointerUp( ev: PointerEvent ) {
		if( !this.dragging ) {
			return;
		}

		this.dragging = false;
		this.canvas.releaseCapture( ev.pointerId );
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
	}
}

/**
 * Renders a FITS image to an offscreen 8-bit canvas via a simple linear min/max stretch --
 * a fixed baseline stretch, not the adjustable-threshold control planned for later.
 */

function renderToOffscreenCanvas( image: FitsImage ): HTMLCanvasElement {
	const { width, height, data, min, max } = image;

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
