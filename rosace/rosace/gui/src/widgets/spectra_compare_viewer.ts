/**
 * Several 1D spectra drawn together, to compare a spectrum with earlier ones
 * of the same star (BeSS validation page). Same toolbar look and gestures as
 * SpectrumViewer, but the curves come as arrays (wavelength, flux) prepared
 * by the server (bess_archive.py), each with its colour, and each can be
 * hidden.
 *
 * "Hα" zooms on the H-alpha region; "Résolution Rosace" swaps each finer
 * spectrum for its copy degraded to the resolution of the spectrum being
 * validated (sent by the server, over the whole spectrum), so the line shapes
 * can be compared.
 */

import { VBox, HBox, Button, Canvas, CanvasEx, BoxProps, Label, Menu } from "x4js";

import { niceTicks, formatTick } from "./spectrum_viewer";

const WHEEL_ZOOM_FACTOR = 1.15;
const MIN_VISIBLE_SPAN = 5;			// angstrom, narrowest zoom
const SELECT_CLICK_THRESHOLD = 3;	// canvas pixels -- below, a left drag is a plain click
const FLUX_MARGIN = 0.06;			// fraction of the flux range added above and below the curves
const HALPHA_VIEW: [ number, number ] = [ 6500, 6650 ];

const LEFT_BUTTON = 0;
const RIGHT_BUTTON = 2;

const HELP_LINES = [
	"Clic gauche + glisser : tracer une zone",
	"Double-clic : libérer la zone",
	"Clic droit + glisser : déplacer",
	"Clic droit : menu (agrandir la zone, Hα, tout)",
	"Ctrl + molette : zoom en longueur d'onde",
	"Hα : zoom sur Hα",
	"Résolution Rosace : spectres plus résolus ramenés à celle du spectre validé",
	"Le flux s'ajuste aux spectres visibles",
];

const MARGIN = { left: 58, right: 14, top: 12, bottom: 34 };
const TICK_SPACING_PX = { x: 90, y: 45 };

interface Series {
	wavelength: Float64Array;
	flux: Float64Array;
}

export interface CompareCurve {
	key: string;
	color: string;					// CSS colour, or a CSS variable name ("--cmp-bess-1")
	width: number;					// line width, canvas pixels
	wavelength: ( number | null )[];
	flux: ( number | null )[];
	degraded?: { wavelength: ( number | null )[], flux: ( number | null )[] } | null;
}

function series( wavelength: ( number | null )[], flux: ( number | null )[] ): Series {
	const n = Math.min( wavelength.length, flux.length );
	const s = { wavelength: new Float64Array( n ), flux: new Float64Array( n ) };
	for( let i = 0; i < n; i++ ) {
		s.wavelength[i] = wavelength[i] ?? NaN;
		s.flux[i] = flux[i] ?? NaN;
	}
	return s;
}

interface Curve {
	key: string;
	color: string;
	width: number;
	full: Series;
	degraded: Series | null;
	visible: boolean;
}

export class SpectraCompareViewer extends VBox {

	private canvas: Canvas;
	private zoneButton: Button;
	private degradeButton: Button;
	private readout: Label;
	private lastReadout = "";

	private curves: Curve[] = [];
	private degradedOn = false;

	private viewMin = 0;
	private viewMax = 1;

	private dragging = false;
	private dragMoved = false;
	private dragStartX = 0;
	private dragOriginMin = 0;
	private dragOriginMax = 0;

	private selecting = false;
	private selectStartX = 0;
	private selectEndX = 0;
	private zone: [ number, number ] | null = null;

	constructor( props: BoxProps = {} ) {
		super( props );
		this.addClass( "fits-viewer" );
		this.addClass( "spectrum-viewer" );

		this.setContent( [
			new HBox({ cls: "fits-toolbar", content: [
				new Button({ cls: "fits-btn", label: "Tout", click: () => this.zoomFit() }),
				new Button({ cls: "fits-btn", label: "Hα", click: () => this.setView( ...HALPHA_VIEW ) }),
				this.degradeButton = new Button({ cls: "fits-btn", label: "Résolution Rosace",
					tooltip: "Spectres plus résolus ramenés à la résolution du spectre validé",
					click: () => this.toggleDegraded() }),
				this.zoneButton = new Button({ cls: "fits-btn", label: "Zone", tooltip: "Jaune : une zone est tracée ; clic : la libérer", click: () => this.clearZone() }),
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
		this.canvas.addDOMEvent( "contextmenu", ( ev: MouseEvent ) => ev.preventDefault() );
		this.canvas.addDOMEvent( "dblclick", () => this.clearZone() );
		this.updateDegradeButton();
	}

	/** Sets the curves (all visible), drawn in this order, and shows them whole. */

	setCurves( curves: CompareCurve[] ) {
		this.curves = curves.map( c => ( {
			key: c.key, color: c.color, width: c.width, visible: true,
			full: series( c.wavelength, c.flux ),
			degraded: c.degraded ? series( c.degraded.wavelength, c.degraded.flux ) : null,
		} ) );
		this.zoomFit();
	}

	/** Shows or hides one curve. */

	setVisible( key: string, visible: boolean ) {
		const curve = this.curves.find( c => c.key === key );
		if( curve ) {
			curve.visible = visible;
			this.canvas.redraw();
		}
	}

	/** Whole range of the visible curves. */

	zoomFit() {
		let lo = Infinity;
		let hi = -Infinity;
		for( const c of this.curves.filter( c => c.visible ) ) {
			const w = c.full.wavelength;
			if( w.length ) {
				lo = Math.min( lo, w[0] );
				hi = Math.max( hi, w[w.length - 1] );
			}
		}
		if( isFinite( lo ) && hi > lo ) {
			this.viewMin = lo;
			this.viewMax = hi;
		}
		this.updateDegradeButton();
		this.canvas.redraw();
	}

	private setView( min: number, max: number ) {
		const span = Math.max( max - min, MIN_VISIBLE_SPAN );
		const centre = ( min + max ) / 2;
		this.viewMin = centre - span / 2;
		this.viewMax = centre + span / 2;
		this.updateDegradeButton();
		this.canvas.redraw();
	}

	/** "Résolution Rosace" is offered when at least one curve has a degraded copy. */

	private updateDegradeButton() {
		const available = this.curves.some( c => c.degraded );
		if( !available ) {
			this.degradedOn = false;
		}
		this.degradeButton.enable( available );
		this.degradeButton.setClass( "active", this.degradedOn );
	}

	private toggleDegraded() {
		this.degradedOn = !this.degradedOn;
		this.updateDegradeButton();
		this.canvas.redraw();
	}

	private shown( c: Curve ): Series {
		return this.degradedOn && c.degraded ? c.degraded : c.full;
	}

	private setZone( zone: [ number, number ] | null ) {
		this.zone = zone;
		this.zoneButton.setClass( "active", zone !== null );
	}

	private clearZone() {
		this.setZone( null );
		this.canvas.redraw();
	}

	private showHelp() {
		new Menu({ items: HELP_LINES }).displayNear( this.canvas.getBoundingRect(), "center middle", "center middle" );
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

	/** Flux range of the visible curves inside the view, with a margin. */

	private fluxRange(): [ number, number ] {
		let lo = Infinity;
		let hi = -Infinity;
		for( const c of this.curves.filter( c => c.visible ) ) {
			const s = this.shown( c );
			for( let i = 0; i < s.wavelength.length; i++ ) {
				const w = s.wavelength[i];
				const v = s.flux[i];
				if( w >= this.viewMin && w <= this.viewMax && isFinite( v ) ) {
					lo = Math.min( lo, v );
					hi = Math.max( hi, v );
				}
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
		// inside a page that scrolls: the wheel scrolls it, Ctrl + wheel zooms
		if( !this.curves.length || !ev.ctrlKey ) {
			return;
		}
		ev.preventDefault();
		const at = this.toWavelength( this.canvasX( ev ) );
		const factor = ev.deltaY < 0 ? 1 / WHEEL_ZOOM_FACTOR : WHEEL_ZOOM_FACTOR;
		this.setView( at - ( at - this.viewMin ) * factor, at + ( this.viewMax - at ) * factor );
		this.updateReadout( ev );
	}

	private onPointerDown( ev: PointerEvent ) {
		if( !this.curves.length ) {
			return;
		}
		if( ev.button === LEFT_BUTTON ) {
			this.canvas.setCapture( ev.pointerId );
			this.canvas.setClass( "zone-mode", true );
			this.selecting = true;
			this.selectStartX = this.selectEndX = this.canvasX( ev );
		}
		else if( ev.button === RIGHT_BUTTON ) {
			this.canvas.setCapture( ev.pointerId );
			this.canvas.setClass( "panning", true );
			this.dragging = true;
			this.dragMoved = false;
			this.dragStartX = ev.pageX;
			this.dragOriginMin = this.viewMin;
			this.dragOriginMax = this.viewMax;
		}
	}

	private onPointerMove( ev: PointerEvent ) {
		if( this.selecting ) {
			this.selectEndX = this.canvasX( ev );
			this.canvas.redraw();
		}
		else if( this.dragging ) {
			if( Math.abs( ev.pageX - this.dragStartX ) > SELECT_CLICK_THRESHOLD ) {
				this.dragMoved = true;
			}
			const shift = ( ev.pageX - this.dragStartX ) / this.plotWidth() * ( this.dragOriginMax - this.dragOriginMin );
			this.setView( this.dragOriginMin - shift, this.dragOriginMax - shift );
		}
		this.updateReadout( ev );
	}

	private onPointerUp( ev: PointerEvent ) {
		this.canvas.releaseCapture( ev.pointerId );
		if( this.selecting ) {
			this.selecting = false;
			this.canvas.setClass( "zone-mode", false );
			if( Math.abs( this.selectEndX - this.selectStartX ) > SELECT_CLICK_THRESHOLD ) {
				const a = this.toWavelength( this.selectStartX );
				const b = this.toWavelength( this.selectEndX );
				this.setZone( [ Math.min( a, b ), Math.max( a, b ) ] );
			}
			this.canvas.redraw();
			return;
		}
		if( this.dragging ) {
			this.dragging = false;
			this.canvas.setClass( "panning", false );
			if( !this.dragMoved ) {
				new Menu({ items: [
					{ text: "Agrandir zone", disabled: this.zone === null, click: () => this.zone && this.setView( ...this.zone ) },
					{ text: "Hα", click: () => this.setView( ...HALPHA_VIEW ) },
					{ text: "Tout", click: () => this.zoomFit() },
				]}).displayAt( ev.pageX, ev.pageY );
			}
		}
	}

	private updateReadout( ev: MouseEvent ) {
		const x = this.canvasX( ev );
		if( !this.curves.length || x < MARGIN.left || x > MARGIN.left + this.plotWidth() ) {
			this.setReadout( "" );
			return;
		}
		this.setReadout( `λ: ${this.toWavelength( x ).toFixed( 1 )} Å` );
	}

	private setReadout( text: string ) {
		if( text !== this.lastReadout ) {
			this.lastReadout = text;
			this.readout.setText( text );
		}
	}

	// ::: Drawing ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

	private paint( ctx: CanvasEx ) {
		const style = getComputedStyle( this.dom );
		const color = ( name: string, fallback: string ) => style.getPropertyValue( name ).trim() || fallback;
		const text = color( "--text", "#3a352d" );
		const muted = color( "--muted", "#7c7364" );
		const grid = color( "--border", "rgba(58, 53, 45, 0.22)" );

		ctx.font = "11px sans-serif";
		if( !this.curves.length ) {
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

		// the curves, the first one (the spectrum being validated) on top
		for( const c of [ ...this.curves ].reverse() ) {
			if( !c.visible ) {
				continue;
			}
			const s = this.shown( c );
			ctx.strokeStyle = c.color.startsWith( "--" ) ? color( c.color, "#888" ) : c.color;
			ctx.lineWidth = c.width;
			ctx.beginPath();
			let penDown = false;
			for( let i = 0; i < s.wavelength.length; i++ ) {
				const w = s.wavelength[i];
				const v = s.flux[i];
				if( !isFinite( v ) || !isFinite( w ) ) {
					penDown = false;
					continue;
				}
				// one point beyond each edge, so the curve reaches the frame
				if( w < this.viewMin && ( i + 1 >= s.wavelength.length || s.wavelength[i + 1] < this.viewMin ) ) {
					continue;
				}
				const x = this.toCanvasX( w );
				const y = toY( v );
				if( penDown ) {
					ctx.lineTo( x, y );
				}
				else {
					ctx.moveTo( x, y );
					penDown = true;
				}
				if( w > this.viewMax ) {
					break;
				}
			}
			ctx.stroke();
		}

		ctx.fillStyle = "rgba(255, 204, 0, 0.18)";
		if( this.selecting ) {
			const x0 = Math.min( this.selectStartX, this.selectEndX );
			ctx.fillRect( x0, top, Math.abs( this.selectEndX - this.selectStartX ), bottom - top );
		}
		else if( this.zone ) {
			const x0 = this.toCanvasX( this.zone[0] );
			ctx.fillRect( x0, top, this.toCanvasX( this.zone[1] ) - x0, bottom - top );
		}
		ctx.restore();

		ctx.strokeStyle = muted;
		ctx.lineWidth = 1;
		ctx.strokeRect( left, top, right - left, bottom - top );
		ctx.fillStyle = text;
		ctx.textAlign = "center";
		ctx.textBaseline = "top";
		for( const t of xTicks ) {
			ctx.fillText( formatTick( t, xTicks ), this.toCanvasX( t ), bottom + 4 );
		}
		ctx.fillText( "Longueur d'onde (Å)", ( left + right ) / 2, bottom + 18 );
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
