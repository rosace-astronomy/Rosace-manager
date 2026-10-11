/**
 * 1D spectrum viewer widget -- the twin of FitsViewer for a spectrum: same
 * toolbar look (fits-toolbar / fits-btn / fits-readout classes) and the same
 * mouse gestures, but a curve on wavelength / flux axes instead of an image.
 *
 * The spectrum is a 1D FITS (read by fits_parser as an image one row high)
 * with a linear wavelength axis: CRVAL1 + (i + 1 - CRPIX1) * CDELT1, in CUNIT1.
 * The overlay sent with it (see image_broadcast.py) gives the title, the
 * lines to mark and the dispersion law's coverage -- outside it, the
 * wavelengths are extrapolated and drawn shaded.
 *
 * Wheel zooms in wavelength around the cursor, drag pans in wavelength, and
 * the flux axis always fits the visible part of the spectrum.
 */

import { VBox, HBox, Button, Canvas, CanvasEx, BoxProps, Label, Menu } from "x4js";

import { parseFits, FitsImage } from "./fits_parser";

const WHEEL_ZOOM_FACTOR = 1.15;
const MIN_VISIBLE_POINTS = 10;		// narrowest zoom, in spectrum points
const SELECT_CLICK_THRESHOLD = 3;	// canvas pixels -- below, a "Zone" gesture is a plain click
const FLUX_MARGIN = 0.06;			// fraction of the flux range added above and below the curve

// Plot area margins (canvas pixels): room for the axis labels and the line names
const MARGIN = { left: 58, right: 14, top: 20, bottom: 34 };
const TICK_SPACING_PX = { x: 90, y: 45 };

/** Overlay sent with the spectrum (reduction_helpers.publish_spectrum_preview()). */
export interface SpectrumOverlay {
	title?: string;
	lines?: { name: string, wavelength: number }[];
	coverage?: [ number, number ] | null;
}

export interface SpectrumViewerProps extends BoxProps {
}

export class SpectrumViewer extends VBox {

	private canvas: Canvas;
	private zoneButton: Button;
	private titleLabel: Label;
	private readout: Label;
	private lastReadout = "";

	private spectrum: FitsImage | null = null;
	private wavelengths: Float64Array = new Float64Array( 0 );
	private unit = "Å";
	private overlay: SpectrumOverlay | null = null;

	// visible wavelength range
	private viewMin = 0;
	private viewMax = 1;

	private dragging = false;
	private dragStartX = 0;
	private dragOriginMin = 0;
	private dragOriginMax = 0;

	// "Zone" mode: drag selects a wavelength range, zoomed on release
	private selectionMode = false;
	private selecting = false;
	private selectStartX = 0;
	private selectEndX = 0;

	constructor( props: SpectrumViewerProps = {} ) {
		super( props );
		this.addClass( "fits-viewer" );
		this.addClass( "spectrum-viewer" );

		this.setContent( [
			new HBox({ cls: "fits-toolbar", content: [
				new Button({ cls: "fits-btn", label: "Tout", click: () => this.zoomFit() }),
				this.zoneButton = new Button({ cls: "fits-btn", label: "Zone", click: () => this.toggleZoneMode() }),
				this.titleLabel = new Label({ cls: "spectrum-title", flex: 1, text: "" }),
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

	/** Parses a 1D FITS spectrum and shows it whole. */

	loadFits( buffer: ArrayBuffer ) {
		const spectrum = parseFits( buffer );
		const h = spectrum.header;
		const crval = parseFloat( h["CRVAL1"] );
		const cdelt = parseFloat( h["CDELT1"] );
		const crpix = h["CRPIX1"] !== undefined ? parseFloat( h["CRPIX1"] ) : 1;
		if( spectrum.height !== 1 || !isFinite( crval ) || !isFinite( cdelt ) ) {
			throw new Error( "Not a 1D spectrum with a linear wavelength axis" );
		}

		this.spectrum = spectrum;
		this.wavelengths = new Float64Array( spectrum.width );
		for( let i = 0; i < spectrum.width; i++ ) {
			this.wavelengths[i] = crval + ( i + 1 - crpix ) * cdelt;
		}
		this.unit = ( h["CUNIT1"] ?? "Angstrom" ) === "nm" ? "nm" : "Å";
		this.setReadout( "" );
		this.zoomFit();
	}

	/** Sets (or clears, with null) the title, lines and coverage drawn with the spectrum. */

	setOverlay( overlay: SpectrumOverlay | null ) {
		this.overlay = overlay;
		this.titleLabel.setText( overlay?.title ?? "" );
		this.canvas.redraw();
	}

	/** Shows the whole spectrum. */

	zoomFit() {
		if( !this.spectrum ) {
			return;
		}
		this.viewMin = this.firstWavelength();
		this.viewMax = this.lastWavelength();
		this.canvas.redraw();
	}

	private toggleZoneMode() {
		this.selectionMode = !this.selectionMode;
		this.zoneButton.setClass( "active", this.selectionMode );
		this.canvas.setClass( "zone-mode", this.selectionMode );
	}

	private firstWavelength() {
		return Math.min( this.wavelengths[0], this.wavelengths[this.wavelengths.length - 1] );
	}

	private lastWavelength() {
		return Math.max( this.wavelengths[0], this.wavelengths[this.wavelengths.length - 1] );
	}

	/** Sets the visible range, kept inside the spectrum and at least MIN_VISIBLE_POINTS wide. */

	private setView( min: number, max: number ) {
		const first = this.firstWavelength();
		const last = this.lastWavelength();
		const step = ( last - first ) / Math.max( 1, this.wavelengths.length - 1 );
		const span = Math.min( last - first, Math.max( max - min, step * MIN_VISIBLE_POINTS ) );
		let lo = ( min + max ) / 2 - span / 2;
		lo = Math.max( first, Math.min( lo, last - span ) );
		this.viewMin = lo;
		this.viewMax = lo + span;
		this.canvas.redraw();
	}

	// ::: Coordinates ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

	private plotWidth() {
		return Math.max( 1, this.canvas.getBoundingRect().width - MARGIN.left - MARGIN.right );
	}

	private toCanvasX( wavelength: number ) {
		return MARGIN.left + ( wavelength - this.viewMin ) / ( this.viewMax - this.viewMin ) * this.plotWidth();
	}

	private toWavelength( canvasX: number ) {
		return this.viewMin + ( canvasX - MARGIN.left ) / this.plotWidth() * ( this.viewMax - this.viewMin );
	}

	/** Index of the spectrum point nearest to a wavelength. */

	private indexOf( wavelength: number ) {
		const n = this.wavelengths.length;
		const step = ( this.wavelengths[n - 1] - this.wavelengths[0] ) / Math.max( 1, n - 1 );
		const i = Math.round( ( wavelength - this.wavelengths[0] ) / step );
		return Math.max( 0, Math.min( n - 1, i ) );
	}

	/** Flux range of the visible part, with a margin above and below. */

	private fluxRange(): [ number, number ] {
		const data = this.spectrum.data;
		const i0 = Math.min( this.indexOf( this.viewMin ), this.indexOf( this.viewMax ) );
		const i1 = Math.max( this.indexOf( this.viewMin ), this.indexOf( this.viewMax ) );
		let lo = Infinity;
		let hi = -Infinity;
		for( let i = i0; i <= i1; i++ ) {
			const v = data[i];
			if( isFinite( v ) ) {
				lo = Math.min( lo, v );
				hi = Math.max( hi, v );
			}
		}
		if( !isFinite( lo ) ) {
			return [ 0, 1 ];
		}
		if( hi === lo ) {
			hi = lo + 1;
		}
		const pad = ( hi - lo ) * FLUX_MARGIN;
		return [ lo - pad, hi + pad ];
	}

	// ::: Mouse ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

	private canvasX( ev: MouseEvent ) {
		return ev.pageX - this.canvas.getBoundingRect().left;
	}

	private onWheel( ev: WheelEvent ) {
		if( !this.spectrum ) {
			return;
		}
		ev.preventDefault();

		// wavelength under the cursor stays under the cursor
		const at = this.toWavelength( this.canvasX( ev ) );
		const factor = ev.deltaY < 0 ? 1 / WHEEL_ZOOM_FACTOR : WHEEL_ZOOM_FACTOR;
		const min = at - ( at - this.viewMin ) * factor;
		const max = at + ( this.viewMax - at ) * factor;
		this.setView( min, max );
		this.updateReadout( ev );
	}

	private onPointerDown( ev: PointerEvent ) {
		if( !this.spectrum ) {
			return;
		}
		this.canvas.setCapture( ev.pointerId );
		if( this.selectionMode ) {
			this.selecting = true;
			this.selectStartX = this.selectEndX = this.canvasX( ev );
			return;
		}
		this.dragging = true;
		this.dragStartX = ev.pageX;
		this.dragOriginMin = this.viewMin;
		this.dragOriginMax = this.viewMax;
	}

	private onPointerMove( ev: PointerEvent ) {
		if( this.selecting ) {
			this.selectEndX = this.canvasX( ev );
			this.canvas.redraw();
		}
		else if( this.dragging ) {
			const shift = ( ev.pageX - this.dragStartX ) / this.plotWidth() * ( this.dragOriginMax - this.dragOriginMin );
			this.setView( this.dragOriginMin - shift, this.dragOriginMax - shift );
		}
		this.updateReadout( ev );
	}

	private onPointerUp( ev: PointerEvent ) {
		this.canvas.releaseCapture( ev.pointerId );
		if( this.selecting ) {
			this.selecting = false;
			if( Math.abs( this.selectEndX - this.selectStartX ) > SELECT_CLICK_THRESHOLD ) {
				const a = this.toWavelength( this.selectStartX );
				const b = this.toWavelength( this.selectEndX );
				this.setView( Math.min( a, b ), Math.max( a, b ) );
			}
			else {
				this.canvas.redraw();
			}
			return;
		}
		this.dragging = false;
	}

	/** Shows the wavelength and flux of the spectrum point under the cursor,
	 * marked "(extrapolé)" outside the dispersion law's coverage (the shaded parts). */

	private updateReadout( ev: MouseEvent ) {
		const x = this.canvasX( ev );
		if( !this.spectrum || x < MARGIN.left || x > MARGIN.left + this.plotWidth() ) {
			this.setReadout( "" );
			return;
		}
		const i = this.indexOf( this.toWavelength( x ) );
		const wavelength = this.wavelengths[i];
		const flux = this.spectrum.data[i];
		const coverage = this.overlay?.coverage;
		const extrapolated = coverage && ( wavelength < coverage[0] || wavelength > coverage[1] ) ? "  (extrapolé)" : "";
		this.setReadout( `λ: ${wavelength.toFixed( 1 )} ${this.unit}  F: ${isFinite( flux ) ? flux.toPrecision( 4 ) : "—"}${extrapolated}` );
	}

	private setReadout( text: string ) {
		if( text !== this.lastReadout ) {
			this.lastReadout = text;
			this.readout.setText( text );
		}
	}

	private onContextMenu( ev: MouseEvent ) {
		if( !this.spectrum ) {
			return;
		}
		const menu = new Menu({ items: [
			{ text: "Tout", click: () => this.zoomFit() },
			{ text: this.selectionMode ? "Désactiver Zone" : "Activer Zone", click: () => this.toggleZoneMode() },
			"-",
			"Molette : zoom en longueur d'onde",
			"Glisser : déplacer le spectre",
			"Zone + glisser : agrandir un intervalle",
			"Le flux s'ajuste à la partie visible",
		]});
		menu.displayAt( ev.pageX, ev.pageY );
		ev.stopPropagation();
		ev.preventDefault();
	}

	// ::: Drawing ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

	private paint( ctx: CanvasEx ) {
		const style = getComputedStyle( this.dom );
		const color = ( name: string, fallback: string ) => style.getPropertyValue( name ).trim() || fallback;
		const text = color( "--text", "#3a352d" );
		const muted = color( "--muted", "#7c7364" );
		const grid = color( "--border", "rgba(58, 53, 45, 0.22)" );
		const curve = color( "--accent", "#3f6b45" );
		const lineColor = color( "--red", "#8f4a34" );

		ctx.font = "11px sans-serif";
		if( !this.spectrum ) {
			ctx.fillStyle = muted;
			ctx.textAlign = "center";
			ctx.fillText( "Aucun spectre", ctx.width / 2, ctx.height / 2 );
			return;
		}

		const left = MARGIN.left;
		const right = ctx.width - MARGIN.right;
		const top = MARGIN.top;
		const bottom = ctx.height - MARGIN.bottom;
		if( right <= left || bottom <= top ) {
			return;
		}
		const [ fluxMin, fluxMax ] = this.fluxRange();
		const toY = ( v: number ) => bottom - ( v - fluxMin ) / ( fluxMax - fluxMin ) * ( bottom - top );

		ctx.save();
		ctx.beginPath();
		ctx.rect( left, top, right - left, bottom - top );
		ctx.clip();

		// extrapolated parts, outside the dispersion law's coverage
		const coverage = this.overlay?.coverage;
		if( coverage ) {
			ctx.fillStyle = "rgba(179, 122, 46, 0.18)";
			const x0 = this.toCanvasX( coverage[0] );
			const x1 = this.toCanvasX( coverage[1] );
			if( x0 > left ) {
				ctx.fillRect( left, top, x0 - left, bottom - top );
			}
			if( x1 < right ) {
				ctx.fillRect( x1, top, right - x1, bottom - top );
			}
		}

		// grid
		ctx.strokeStyle = grid;
		ctx.lineWidth = 1;
		const xTicks = niceTicks( this.viewMin, this.viewMax, ( right - left ) / TICK_SPACING_PX.x );
		const yTicks = niceTicks( fluxMin, fluxMax, ( bottom - top ) / TICK_SPACING_PX.y );
		ctx.beginPath();
		for( const t of xTicks ) {
			const x = Math.round( this.toCanvasX( t ) );
			ctx.moveTo( x, top );
			ctx.lineTo( x, bottom );
		}
		for( const t of yTicks ) {
			const y = Math.round( toY( t ) );
			ctx.moveTo( left, y );
			ctx.lineTo( right, y );
		}
		ctx.stroke();

		// marked lines (Balmer...)
		ctx.strokeStyle = lineColor;
		ctx.setLineDash( [ 5, 4 ] );
		ctx.beginPath();
		for( const line of this.overlay?.lines ?? [] ) {
			const x = Math.round( this.toCanvasX( line.wavelength ) );
			ctx.moveTo( x, top );
			ctx.lineTo( x, bottom );
		}
		ctx.stroke();
		ctx.setLineDash( [] );

		// the spectrum
		ctx.strokeStyle = curve;
		ctx.lineWidth = 1.2;
		ctx.beginPath();
		const data = this.spectrum.data;
		const i0 = Math.max( 0, Math.min( this.indexOf( this.viewMin ), this.indexOf( this.viewMax ) ) - 1 );
		const i1 = Math.min( data.length - 1, Math.max( this.indexOf( this.viewMin ), this.indexOf( this.viewMax ) ) + 1 );
		let penDown = false;
		for( let i = i0; i <= i1; i++ ) {
			if( !isFinite( data[i] ) ) {
				penDown = false;
				continue;
			}
			const x = this.toCanvasX( this.wavelengths[i] );
			const y = toY( data[i] );
			if( penDown ) {
				ctx.lineTo( x, y );
			}
			else {
				ctx.moveTo( x, y );
				penDown = true;
			}
		}
		ctx.stroke();

		// "Zone" selection being drawn
		if( this.selecting ) {
			const x0 = Math.min( this.selectStartX, this.selectEndX );
			ctx.fillStyle = "rgba(255, 204, 0, 0.18)";
			ctx.fillRect( x0, top, Math.abs( this.selectEndX - this.selectStartX ), bottom - top );
		}
		ctx.restore();

		// line names, above the plot area
		ctx.fillStyle = lineColor;
		ctx.textAlign = "center";
		ctx.textBaseline = "bottom";
		for( const line of this.overlay?.lines ?? [] ) {
			const x = this.toCanvasX( line.wavelength );
			if( x >= left && x <= right ) {
				ctx.fillText( line.name, x, top - 3 );
			}
		}

		// frame and axes
		ctx.strokeStyle = muted;
		ctx.lineWidth = 1;
		ctx.strokeRect( left, top, right - left, bottom - top );
		ctx.fillStyle = text;
		ctx.textAlign = "center";
		ctx.textBaseline = "top";
		for( const t of xTicks ) {
			ctx.fillText( formatTick( t, xTicks ), this.toCanvasX( t ), bottom + 4 );
		}
		ctx.fillText( `Longueur d'onde (${this.unit})`, ( left + right ) / 2, bottom + 18 );
		ctx.textAlign = "right";
		ctx.textBaseline = "middle";
		for( const t of yTicks ) {
			ctx.fillText( formatTick( t, yTicks ), left - 5, toY( t ) );
		}
		ctx.save();
		ctx.translate( 12, ( top + bottom ) / 2 );
		ctx.rotate( -Math.PI / 2 );
		ctx.textAlign = "center";
		ctx.fillText( "Flux relatif", 0, 0 );
		ctx.restore();
	}
}

/** "Round" tick values (1, 2 or 5 × 10^n apart) covering [min, max], about `count` of them. */

function niceTicks( min: number, max: number, count: number ): number[] {
	const range = max - min;
	if( !( range > 0 ) || !isFinite( range ) ) {
		return [];
	}
	const rough = range / Math.max( 1, count );
	const power = Math.pow( 10, Math.floor( Math.log10( rough ) ) );
	const step = [ 1, 2, 5, 10 ].map( m => m * power ).find( s => s >= rough ) ?? 10 * power;
	const ticks: number[] = [];
	for( let t = Math.ceil( min / step ) * step; t <= max + step * 1e-9; t += step ) {
		ticks.push( Math.abs( t ) < step * 1e-9 ? 0 : t );
	}
	return ticks;
}

/** Tick label with just enough decimals for the step between ticks. */

function formatTick( value: number, ticks: number[] ): string {
	const step = ticks.length > 1 ? Math.abs( ticks[1] - ticks[0] ) : 1;
	const decimals = Math.max( 0, -Math.floor( Math.log10( step ) ) );
	return value.toFixed( decimals );
}
