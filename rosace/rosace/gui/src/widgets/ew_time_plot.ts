/**
 * H-alpha equivalent width of several spectra against their date (BeSS
 * validation page, "Spectres précédents" panel): one point per spectrum, in
 * its curve's colour, its value written beside it; the spectrum being
 * validated drawn larger. Points can be hidden, like their curves.
 */

import { VBox, BoxProps, Canvas, CanvasEx } from "x4js";

import { niceTicks, formatTick } from "./spectrum_viewer";

const MARGIN = { left: 58, right: 24, top: 16, bottom: 34 };
const TICK_SPACING_PX = { x: 110, y: 40 };
const VALUE_MARGIN = 0.15;			// fraction of the value range added above and below the points
const MIN_VALUE_SPAN = 1;			// angstrom
const SINGLE_DATE_SPAN_DAYS = 30;	// time axis around a lone point
const DAY_MS = 86400000;

export interface EwPoint {
	key: string;
	date: number;		// ms since 1970 (UT)
	ew: number;			// angstrom, negative in emission
	color: string;		// CSS colour, or a CSS variable name ("--cmp-bess-1")
	current: boolean;	// the spectrum being validated
}

export class EwTimePlot extends VBox {

	private canvas: Canvas;
	private points: EwPoint[] = [];
	private hidden = new Set<string>( );

	constructor( props: BoxProps = {} ) {
		super( props );
		this.setContent( [
			this.canvas = new Canvas( { flex: 1, clear: true, paint_cb: ctx => this.draw( ctx ) } ),
		] );
	}

	private repaint( ) {
		this.canvas.redraw( );
	}

	setPoints( points: EwPoint[] ) {
		this.points = points;
		this.hidden.clear( );
		this.repaint( );
	}

	setVisible( key: string, visible: boolean ) {
		if( visible ) {
			this.hidden.delete( key );
		}
		else {
			this.hidden.add( key );
		}
		this.repaint( );
	}

	private draw( ctx: CanvasEx ) {
		const style = getComputedStyle( this.dom );
		const color = ( name: string, fallback: string ) => style.getPropertyValue( name ).trim() || fallback;
		const text = color( "--text", "#3a352d" );
		const muted = color( "--muted", "#7c7364" );
		const grid = color( "--border", "rgba(58, 53, 45, 0.22)" );

		ctx.font = "11px sans-serif";
		const shown = this.points.filter( p => !this.hidden.has( p.key ) );
		if( !shown.length ) {
			ctx.fillStyle = muted;
			ctx.textAlign = "center";
			ctx.fillText( "Aucune largeur équivalente mesurée", ctx.width / 2, ctx.height / 2 );
			return;
		}

		const left = MARGIN.left;
		const right = ctx.width - MARGIN.right;
		const top = MARGIN.top;
		const bottom = ctx.height - MARGIN.bottom;
		if( right <= left || bottom <= top ) {
			return;
		}

		// time axis in days, a little wider than the points
		const days = shown.map( p => p.date / DAY_MS );
		let d0 = Math.min( ...days );
		let d1 = Math.max( ...days );
		if( d1 - d0 < 1 ) {
			d0 -= SINGLE_DATE_SPAN_DAYS / 2;
			d1 += SINGLE_DATE_SPAN_DAYS / 2;
		}
		const pad = ( d1 - d0 ) * 0.06;
		d0 -= pad;
		d1 += pad;

		let v0 = Math.min( ...shown.map( p => p.ew ) );
		let v1 = Math.max( ...shown.map( p => p.ew ) );
		if( v1 - v0 < MIN_VALUE_SPAN ) {
			const mid = ( v0 + v1 ) / 2;
			v0 = mid - MIN_VALUE_SPAN / 2;
			v1 = mid + MIN_VALUE_SPAN / 2;
		}
		const vpad = ( v1 - v0 ) * VALUE_MARGIN;
		v0 -= vpad;
		v1 += vpad;

		const toX = ( day: number ) => left + ( day - d0 ) / ( d1 - d0 ) * ( right - left );
		const toY = ( v: number ) => bottom - ( v - v0 ) / ( v1 - v0 ) * ( bottom - top );

		const xTicks = niceTicks( d0, d1, ( right - left ) / TICK_SPACING_PX.x ).map( Math.round );
		const yTicks = niceTicks( v0, v1, ( bottom - top ) / TICK_SPACING_PX.y );
		ctx.strokeStyle = grid;
		ctx.lineWidth = 1;
		ctx.beginPath();
		for( const t of xTicks ) {
			const x = Math.round( toX( t ) );
			ctx.moveTo( x, top );
			ctx.lineTo( x, bottom );
		}
		for( const t of yTicks ) {
			const y = Math.round( toY( t ) );
			ctx.moveTo( left, y );
			ctx.lineTo( right, y );
		}
		ctx.stroke();

		// points, the spectrum being validated last (on top)
		ctx.textBaseline = "middle";
		for( const p of [ ...shown ].sort( ( a, b ) => Number( a.current ) - Number( b.current ) ) ) {
			const x = toX( p.date / DAY_MS );
			const y = toY( p.ew );
			ctx.beginPath();
			ctx.arc( x, y, p.current ? 6 : 4.5, 0, 2 * Math.PI );
			ctx.fillStyle = p.color.startsWith( "--" ) ? color( p.color, "#888" ) : p.color;
			ctx.fill();
			if( p.current ) {
				ctx.strokeStyle = text;
				ctx.lineWidth = 1.5;
				ctx.stroke();
			}
			ctx.fillStyle = text;
			const label = p.ew.toFixed( 2 );
			const room = right - x > ctx.measureText( label ).width + 12;
			ctx.textAlign = room ? "left" : "right";
			ctx.fillText( label, room ? x + 9 : x - 9, y );
		}

		ctx.strokeStyle = muted;
		ctx.lineWidth = 1;
		ctx.strokeRect( left, top, right - left, bottom - top );
		ctx.fillStyle = text;
		ctx.textAlign = "center";
		ctx.textBaseline = "top";
		for( const t of xTicks ) {
			ctx.fillText( new Date( t * DAY_MS ).toLocaleDateString( "fr-FR", { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "2-digit" } ),
				toX( t ), bottom + 4 );
		}
		ctx.fillText( "Date (UT)", ( left + right ) / 2, bottom + 18 );
		ctx.textAlign = "right";
		ctx.textBaseline = "middle";
		for( const t of yTicks ) {
			ctx.fillText( formatTick( t, yTicks ), left - 5, toY( t ) );
		}
		ctx.save();
		ctx.translate( 12, ( top + bottom ) / 2 );
		ctx.rotate( -Math.PI / 2 );
		ctx.textAlign = "center";
		ctx.fillText( "LE Hα (Å)", 0, 0 );
		ctx.restore();
	}
}
