/**
 * BeSS validation page (served at /be/), separate from the main page on
 * purpose: not every installation observes Be stars, so the main page never
 * links here. Two views, chosen by the address:
 * - /be/            the Be spectra not validated in BeSS yet, grouped by
 *                   session (GET /bess/validations, filled by the
 *                   bess_validation REDUCTION step); an accepted one is
 *                   marked "sent" here, once sent to BeSS by hand;
 * - /be/#<obs_id>   one spectrum, with everything needed to accept or reject
 *                   it (GET /bess/validations/<obs_id>, prepared by the same
 *                   step) and the decision bar at the top, plus the earlier
 *                   spectra of the star, searched in BeSS when the page
 *                   opens (GET /bess/validations/<obs_id>/previous).
 * Same login (cookie) and same look as the main page.
 */

import { VBox, HBox, Button, Label, Application, BoxProps, Input, Image, Link, MessageBox, Checkbox, initTooltips } from "x4js";

import "./be_validation.scss";

import { LoginView, fetchCurrentUser, logout as authLogout } from "./auth";
import { FitsViewer, ImageOverlay } from "./widgets/fits_viewer";
import { SpectrumViewer, SpectrumOverlay } from "./widgets/spectrum_viewer";
import { SpectraCompareViewer, CompareCurve } from "./widgets/spectra_compare_viewer";
import { EwTimePlot } from "./widgets/ew_time_plot";

import logout_icon from "./assets/logout.svg";

import "@fontsource/montserrat/400.css"
import "@fontsource/montserrat/700.css"

const API = "";
const PAGE_TITLE = "Rosace — Validation des spectres Be";

type Status = "pending" | "accepted" | "rejected" | "sent" | "bess_validated";

interface ValidationRow {
	observation_id: string;
	session_id: string;
	star: string;
	final_spectrum: string;
	status: Status;
	decided_at: string | null;
	decided_by: string | null;
	comment: string | null;
	sent_at: string | null;
	observed_at: string | null;
	session_started_at: string | null;
}

/** Written by the bess_validation REDUCTION step ("<final>_validation.json"). */
interface PageData {
	header: {
		star: string; magnitude_v: number | null; spectral_type: string | null;
		date_obs: string | null; date_end: string | null; exposures: string | null; exptime_s: number | null;
		program: string | null; priority: number | null; session_id: string; observation_id: string;
		final_spectrum: string;
	} | null;
	quality: {
		unit: "A" | "nm";
		resolving_power: number | null; resolving_power_wavelength: number | null;
		snr: { wavelength_min: number, wavelength_max: number, snr: number }[];
		guiding: {
			total_rms_arcsec: number, ra_rms_arcsec: number, dec_rms_arcsec: number,
			ra_max_arcsec: number, dec_max_arcsec: number, frames: number, dropped: number,
		} | null;
		star_slit_offset_arcsec: number | null;
		rejected_frames: string[];
		frames: number;
	} | null;
	spectrum: { file: string, overlay: SpectrumOverlay | null };
	guide: { file: string, overlay: ImageOverlay } | null;
	frame2d?: { file: string, overlay: ImageOverlay } | null;	// absent from page data prepared before it existed
	sky?: {
		fripon: {
			file: string; station: string; capture_time: string; offset_s: number;
			model: "fitted" | "stored" | null; matched_stars: number | null;
		};
	} | null;
	plots: { guiding: string | null, frames: string | null };
	warnings: { phase: string, time: string, level: string, message: string }[] | null;
	summary_pdf: string | null;
	files: string[];
	unavailable: Record<string, string>;
}

/** One spectrum of the "Spectres précédents" panel (bess_archive.previous_spectra()). */
interface PreviousSpectrum {
	source: "current" | "rosace" | "bess" | "bess_rosace";
	date_obs: string | null;
	observer: string | null;
	instrument: string | null;
	resolving_power: number | null;
	wavelength: ( number | null )[];
	flux: ( number | null )[];
	degraded: { wavelength: ( number | null )[], flux: ( number | null )[] } | null;
	halpha_ew: number | null;	// angstrom, at the validated spectrum's resolution, negative in emission
	scaled: boolean;			// set to 1 on the continuum on either side of H-alpha, like the others
	observation_id?: string;
	bess_target_name?: string;
}

interface PreviousSpectra {
	found_by: "robot" | "name" | "position" | null;	// how BeSS recognised the star
	bess_error: string | null;
	current: PreviousSpectrum;
	spectra: PreviousSpectrum[];
}

const SOURCE_LABEL: Record<PreviousSpectrum["source"], string> = {
	current:     "spectre à valider",
	rosace:      "Rosace",
	bess:        "BeSS",
	bess_rosace: "BeSS (spectre Rosace)",
};

// Colour variables of each source (be_validation.scss), by rank within the source
const SOURCE_COLORS: Record<PreviousSpectrum["source"], string> = {
	current:     "--cmp-current",
	rosace:      "--cmp-rosace",
	bess:        "--cmp-bess",
	bess_rosace: "--cmp-bess-rosace",
};
const COLOR_SHADES = 5;

interface SpectrumPage extends ValidationRow {
	data: PageData | null;
}

const STATUS_LABEL: Record<Status, string> = {
	pending:        "À valider",
	accepted:       "Accepté",
	rejected:       "Refusé",
	sent:           "Envoyé à BeSS",
	bess_validated: "Validé dans BeSS",
};

class Unauthorized extends Error {}

async function apiGet<T>( path: string ): Promise<T> {
	const res = await fetch( `${API}${path}`, { cache: "no-store", signal: AbortSignal.timeout( 10000 ) } );
	if( res.status === 401 ) {
		throw new Unauthorized( );
	}
	if( !res.ok ) {
		throw new Error( `${res.status} ${res.statusText}` );
	}
	return await res.json( ) as T;
}

/** URL of one file of a spectrum's page (only the files its page data names are served). */
function fileUrl( obsId: string, name: string ): string {
	return `${API}/bess/validations/${encodeURIComponent( obsId )}/files/${name.split( "/" ).map( encodeURIComponent ).join( "/" )}`;
}

/** Local date of a session's start, e.g. "17/10/2026" ("date inconnue" when absent). */
function sessionDate( iso: string | null ): string {
	return iso ? new Date( iso ).toLocaleDateString( "fr-FR" ) : "date inconnue";
}

/** Local date and time, e.g. "18/10/2026 14:05". */
function localDateTime( iso: string ): string {
	return new Date( iso ).toLocaleString( "fr-FR", { dateStyle: "short", timeStyle: "short" } );
}

/** "2026-10-17T04:02:28.048" -> "2026-10-17 04:02:28" (UT, as written in the FITS header). */
function utText( iso: string | null ): string {
	return iso ? iso.replace( "T", " " ).replace( /\.\d+$/, "" ) : "?";
}

function arcsec( value: number ): string {
	return `${value.toFixed( 2 )}″`;
}

/** The rows grouped by session, in the server's order (most recent session first). */
function groupBySession( rows: ValidationRow[] ): ValidationRow[][] {
	const groups = new Map<string, ValidationRow[]>( );
	for( const row of rows ) {
		if( !groups.has( row.session_id ) ) {
			groups.set( row.session_id, [] );
		}
		groups.get( row.session_id ).push( row );
	}
	return [ ...groups.values( ) ];
}

function statusChip( status: Status ): Label {
	return new Label( { cls: `status ${status}`, align: "center", text: STATUS_LABEL[status] ?? status } );
}

/** A panel with its title bar, like the main page's panels. */
function panel( title: string, cls: string, content: any[] ): VBox {
	return new VBox( { cls: `panel ${cls}`, content: [ new Label( { cls: "title", text: title } ), ...content ] } );
}

/** One "label: value" line; a value not measured is shown in grey. */
function infoLine( label: string, value: string | null ): HBox {
	return new HBox( { cls: "info-line", content: [
		new Label( { cls: "info-label", text: label } ),
		new Label( { cls: value === null ? "info-value missing" : "info-value", text: value ?? "non mesuré" } ),
	] } );
}

interface ViewProps extends BoxProps {
	onUnauthorized: ( ) => void;
}

/** The header bar shared by both views. */
function pageHeader( extra: any[] ): HBox {
	return new HBox( { cls: "appheader", content: [
		new HBox( { cls: "header-logo", content: [ new Label( { id: "title", text: PAGE_TITLE } ) ] } ),
		new HBox( { flex: 1 } ),
		...extra,
	] } );
}

class ListView extends VBox {

	private list: VBox;

	constructor( props: ViewProps ) {
		super( { cls: "validation-view", ...props } );

		this.setContent( [
			pageHeader( [
				new Button( { id: "refresh", label: "Rafraîchir", click: ( ) => this.refresh( ) } ),
				new Button( { id: "logout", icon: logout_icon, tooltip: "Déconnexion", click: ( ) => { authLogout( ).then( props.onUnauthorized ); } } ),
			] ),
			this.list = new VBox( { cls: "validation-list", flex: 1 } ),
		] );

		this.refresh( );
	}

	async refresh( ) {
		let rows: ValidationRow[];
		try {
			rows = await apiGet<ValidationRow[]>( "/bess/validations" );
		}
		catch( e ) {
			if( e instanceof Unauthorized ) {
				( this.props as ViewProps ).onUnauthorized( );
				return;
			}
			// shown in the list itself, not only in a passing notification
			this.list.setContent( [ new Label( { cls: "list-message error", text: `Liste indisponible : ${e}` } ) ] );
			return;
		}

		if( rows.length === 0 ) {
			this.list.setContent( [ new Label( { cls: "list-message", text: "Aucun spectre en attente de validation dans BeSS." } ) ] );
			return;
		}

		this.list.setContent( groupBySession( rows ).map( group => {
			const first = group[0];
			const count = group.length > 1 ? `${group.length} spectres` : "1 spectre";
			return new VBox( { cls: "panel session", content: [
				new Label( { cls: "title", text: `Session du ${sessionDate( first.session_started_at )} — ${first.session_id} — ${count}` } ),
				// Fixed-width columns (be_validation.scss), aligned across rows and sessions
				...group.map( row => new HBox( { cls: "spectrum-row", content: [
					new Button( { cls: "open", label: "Ouvrir", tooltip: "Ouvrir la page de ce spectre",
						click: ( ) => { location.hash = row.observation_id; } } ),
					new Label( { cls: "star", text: row.star } ),
					new Label( { cls: "obs-id", text: row.observation_id } ),
					statusChip( row.status ),
					new HBox( { cls: "row-action", content: row.status === "accepted"
						? [ new Button( { cls: "sent", label: "Envoyé", tooltip: "À cliquer une fois le spectre envoyé à BeSS",
							click: ( ) => this.markSent( row ) } ) ]
						: [] } ),
					// the rest of the width; the whole comment in the tooltip
					new Label( { cls: "row-comment", text: row.comment ?? "", tooltip: row.comment ?? undefined } ),
				] } ) ),
			] } );
		} ) );
	}

	/** Records that the observer sent this accepted spectrum to BeSS, after confirmation. */
	private async markSent( row: ValidationRow ) {
		const answer = await MessageBox.showAsync(
			`${row.star} (${row.observation_id}) a-t-il bien été envoyé à BeSS ?`,
			[ new Button( { id: "confirm", label: "Oui, envoyé" } ), new Button( { id: "cancel", label: "Non" } ) ],
			"Spectre envoyé à BeSS", "question" );
		if( answer !== "confirm" ) {
			return;
		}
		try {
			const res = await fetch( `${API}/bess/validations/${encodeURIComponent( row.observation_id )}/sent`,
				{ method: "POST", signal: AbortSignal.timeout( 10000 ) } );
			if( res.status === 401 ) {
				( this.props as ViewProps ).onUnauthorized( );
				return;
			}
			if( !res.ok ) {
				const body = await res.json( ).catch( ( ) => ( {} ) );
				throw new Error( body.detail ?? `${res.status} ${res.statusText}` );
			}
		}
		catch( e ) {
			this.list.setContent( [ new Label( { cls: "list-message error", text: `Envoi non enregistré : ${e}` } ) ] );
			return;
		}
		this.refresh( );
	}
}

/**
 * One spectrum: the decision bar at the top (always visible), then
 * everything needed to decide, prepared by the bess_validation step.
 * Accepting, rejecting or taking the decision back ("Remettre à valider")
 * records it and goes back to the list; "Annuler" goes back without
 * changing anything.
 */

class SpectrumView extends VBox {

	private obsId: string;
	private status: Status | null = null;
	private comment: Input;
	private body: VBox;
	private buttons: Button[] = [];
	private reset: Button;
	private message: Label;

	constructor( props: ViewProps & { obsId: string } ) {
		super( { cls: "validation-view", ...props } );
		this.obsId = props.obsId;

		const accept = new Button( { cls: "decide accept", label: "Accepter", click: ( ) => this.decide( "accepted" ) } );
		const reject = new Button( { cls: "decide reject", label: "Refuser", click: ( ) => this.decide( "rejected" ) } );
		this.reset = new Button( { cls: "decide reset", label: "Remettre à valider",
			tooltip: "Annule la décision : le spectre redevient « À valider »", click: ( ) => this.decide( "pending" ) } );
		this.buttons = [ accept, reject, this.reset ];

		this.setContent( [
			pageHeader( [
				new Button( { id: "logout", icon: logout_icon, tooltip: "Déconnexion", click: ( ) => { authLogout( ).then( props.onUnauthorized ); } } ),
			] ),
			new HBox( { cls: "decision-bar", content: [
				new Label( { cls: "comment-label", text: "Commentaire" } ),
				this.comment = new Input( { cls: "comment", type: "text", flex: 1, placeholder: "facultatif" } ),
				accept,
				reject,
				this.reset,
				new Button( { cls: "decide cancel", label: "Annuler", tooltip: "Retour à la liste, sans rien changer", click: ( ) => this.back( ) } ),
			] } ),
			this.message = new Label( { cls: "decision-message" } ),
			this.body = new VBox( { cls: "spectrum-page", flex: 1 } ),
		] );
		this.message.show( false );
		this.buttons.forEach( b => b.disable( ) );

		this.load( );
	}

	private back( ) {
		location.hash = "";
	}

	private showMessage( text: string ) {
		this.message.setText( text );
		this.message.show( true );
	}

	private async load( ) {
		let page: SpectrumPage;
		try {
			page = await apiGet<SpectrumPage>( `/bess/validations/${encodeURIComponent( this.obsId )}` );
		}
		catch( e ) {
			if( e instanceof Unauthorized ) {
				( this.props as ViewProps ).onUnauthorized( );
				return;
			}
			this.showMessage( `Spectre ${this.obsId} indisponible : ${e}` );
			return;
		}

		document.title = `${page.star} — ${PAGE_TITLE}`;
		this.comment.setValue( page.comment ?? "" );
		this.status = page.status;
		const decidable = this.enableButtons( );
		if( !decidable ) {
			this.showMessage( `${STATUS_LABEL[page.status]} : décision figée.` );
		}

		const data = page.data;
		if( !data ) {
			this.body.setContent( [ new Label( { cls: "list-message error",
				text: "Les données de cette page n'ont pas été préparées (commande --build-validation-data)." } ) ] );
			return;
		}

		// The wheel scrolls the page; Ctrl + wheel zooms
		const spectrum = new SpectrumViewer( { flex: 1, ctrlWheel: true } );
		const guide = data.guide ? new FitsViewer( { flex: 1, ctrlWheel: true } ) : null;
		const frame2d = data.frame2d ? new FitsViewer( { flex: 1, ctrlWheel: true } ) : null;
		const previousViewer = new SpectraCompareViewer( { flex: 1 } );
		const previousMessage = new Label( { cls: "previous-message", text: "Recherche des spectres précédents dans BeSS…" } );
		const previousLegend = new VBox( { cls: "previous-legend" } );
		const previousEw = new EwTimePlot( { flex: 1 } );
		const previous = panel( "Spectres précédents", "previous", [
			previousMessage,
			new VBox( { cls: "viewer-box spectrum-box", content: [ previousViewer ] } ),
			previousLegend,
			new Label( { cls: "ew-title", text: "Largeur équivalente Hα, à la résolution du spectre validé (négative en émission)" } ),
			new VBox( { cls: "viewer-box ew-box", content: [ previousEw ] } ),
		] );

		this.body.setContent( [
			this.headerPanel( page, data ),
			panel( "Spectre 1D", "spectrum", [ new VBox( { cls: "viewer-box spectrum-box", content: [ spectrum ] } ) ] ),
			previous,
			panel( "Spectre 2D (dernière pose corrigée)", "frame2d", frame2d
				? [ new VBox( { cls: "viewer-box frame2d-box", content: [ frame2d ] } ),
					new Label( { cls: "frame2d-legend", text: "Cyan : fenêtre du spectre — magenta pointillé : zones de ciel" } ) ]
				: [ data.frame2d === undefined
					? new Label( { cls: "list-message", text: "Non disponible (page préparée avant ce panneau : commande --build-validation-data --apply)" } )
					: this.unavailable( data, "frame2d" ) ] ),
			panel( "Ciel", "sky", [ this.skyRow( data ) ] ),
			new HBox( { cls: "two-panels", content: [
				panel( "Image de guidage", "guide", [ guide
					? new VBox( { cls: "viewer-box guide-box", content: [ guide ] } )
					: this.unavailable( data, "guide" ) ] ),
				panel( "Flux et ciel, pose par pose", "frames", [ this.plot( data, "frames", "frames_plot" ) ] ),
			] } ),
			panel( "Guidage PHD2", "guiding", [ this.plot( data, "guiding", "guiding_plot" ) ] ),
			this.warningsPanel( data ),
		] );

		this.loadSpectrum( spectrum, data );
		this.loadPrevious( previousViewer, previousEw, previousMessage, previousLegend );
		if( guide ) {
			this.loadImage( guide, data.guide, "guide", "Image de guidage" );
		}
		if( frame2d ) {
			this.loadImage( frame2d, data.frame2d, "science", "Image 2D" );
		}
	}

	/** Observation facts on the left, quality indicators on the right, the previous decision below. */
	private headerPanel( page: SpectrumPage, data: PageData ): VBox {
		const h = data.header;
		const q = data.quality;
		const unit = q?.unit === "nm" ? "nm" : "Å";

		const facts: HBox[] = [];
		if( h ) {
			const star = [ h.star, h.magnitude_v !== null ? `V ${h.magnitude_v.toFixed( 2 )}` : null, h.spectral_type ]
				.filter( x => x ).join( " — " );
			facts.push(
				infoLine( "Étoile", star ),
				infoLine( "Date (UT)", `${utText( h.date_obs )} → ${utText( h.date_end )}` ),
				infoLine( "Poses", h.exposures ? `${h.exposures}${q ? `, ${q.frames} poses` : ""}` : null ),
				infoLine( "Programme", `${h.program ?? "?"} (priorité ${h.priority ?? "-"})` ),
				infoLine( "Session / observation", `${h.session_id} / ${h.observation_id}` ),
				infoLine( "Fichier final", h.final_spectrum ),
			);
		}
		if( data.summary_pdf ) {
			const link = new Link( { cls: "pdf-link", href: fileUrl( this.obsId, data.summary_pdf ), text: "Résumé PDF de l'observation" } );
			link.setAttribute( "target", "_blank" );
			facts.push( new HBox( { cls: "info-line", content: [ new Label( { cls: "info-label", text: "" } ), link ] } ) );
		}

		const quality: HBox[] = [];
		if( q ) {
			quality.push( infoLine( "Pouvoir de résolution",
				q.resolving_power !== null ? `R = ${Math.round( q.resolving_power )} à ${q.resolving_power_wavelength} ${unit}` : null ) );
			if( q.snr.length ) {
				q.snr.forEach( z => quality.push( infoLine( `SNR ${z.wavelength_min}–${z.wavelength_max} ${unit}`, `${Math.round( z.snr )}` ) ) );
			}
			else {
				quality.push( infoLine( "SNR", null ) );
			}
			const g = q.guiding;
			quality.push( infoLine( "Guidage (RMS)", g
				? `${arcsec( g.total_rms_arcsec )} (AD ${arcsec( g.ra_rms_arcsec )}, Dec ${arcsec( g.dec_rms_arcsec )})` : null ) );
			quality.push( infoLine( "Guidage (écart max)", g
				? `AD ${arcsec( g.ra_max_arcsec )}, Dec ${arcsec( g.dec_max_arcsec )} — ${g.frames} poses PHD2, ${g.dropped} perdue(s)` : null ) );
			quality.push( infoLine( "Écart étoile – fente", q.star_slit_offset_arcsec !== null ? arcsec( q.star_slit_offset_arcsec ) : null ) );
			quality.push( infoLine( "Rejetées par stack", q.rejected_frames.length ? q.rejected_frames.join( ", " ) : "aucune" ) );
		}

		const decision: any[] = [ new Label( { cls: "info-label", text: "Décision" } ), statusChip( page.status ) ];
		if( page.decided_at ) {
			decision.push( new Label( { cls: "info-value",
				text: `le ${localDateTime( page.decided_at )} par ${page.decided_by ?? "?"}${page.comment ? ` — « ${page.comment} »` : ""}` } ) );
		}

		return panel( page.star, "observation keep-case", [
			new HBox( { cls: "header-columns", content: [
				new VBox( { cls: "facts", flex: 1, content: facts } ),
				new VBox( { cls: "quality", content: quality.length ? quality : [ this.unavailable( data, "quality" ) ] } ),
			] } ),
			new HBox( { cls: "info-line decision", content: decision } ),
		] );
	}

	/** The FRIPON all-sky image (target and bright stars marked by
	 * fripon_image); the wide-field image will take the second column. */
	private skyRow( data: PageData ): HBox {
		const fripon = data.sky?.fripon;
		if( !fripon ) {
			return new HBox( { content: [ data.sky === undefined
				? new Label( { cls: "list-message", text: "Non disponible (page préparée avant ce panneau : commande --build-validation-data --apply)" } )
				: this.unavailable( data, "sky" ) ] } );
		}
		const minutes = Math.round( fripon.offset_s / 60 );
		const positions = fripon.model === "fitted" ? `cible et étoiles placées par le modèle d'objectif ajusté sur ${fripon.matched_stars} étoiles`
			: fripon.model === "stored" ? "cible et étoiles placées par le modèle d'objectif enregistré (trop peu d'étoiles sur l'image)"
			: "pas de modèle d'objectif : cible non placée";
		return new HBox( { cls: "sky-row", content: [
			new VBox( { cls: "sky-column", content: [
				new Image( { cls: "sky-image", src: fileUrl( this.obsId, fripon.file ), fit: "contain" } ),
				new Label( { cls: "sky-caption", text: `FRIPON ${fripon.station} — ${fripon.capture_time.slice( 0, 19 ).replace( "T", " " )} UT `
					+ `(${minutes >= 0 ? "+" : ""}${minutes} min du milieu de l'observation) — ${positions}` } ),
			] } ),
		] } );
	}

	private plot( data: PageData, key: "guiding" | "frames", part: string ) {
		const name = data.plots[key];
		return name
			? new Image( { cls: `plot plot-${key}`, src: fileUrl( this.obsId, name ) } )
			: this.unavailable( data, part );
	}

	private unavailable( data: PageData, part: string ): Label {
		const reason = data.unavailable?.[part];
		return new Label( { cls: "list-message", text: `Non disponible${reason ? ` (${reason})` : ""}` } );
	}

	private warningsPanel( data: PageData ): VBox {
		const events = data.warnings;
		let content: any[];
		if( events === null ) {
			content = [ this.unavailable( data, "warnings" ) ];
		}
		else if( events.length === 0 ) {
			content = [ new Label( { cls: "list-message", text: "Aucun (ou plus dans le journal, s'il a tourné depuis : voir le résumé PDF)." } ) ];
		}
		else {
			content = events.map( e => new Label( { cls: `log-line ${e.level === "WARNING" ? "warning" : "error"}`,
				text: `${e.time}  ${e.phase}  ${e.level}  ${e.message}` } ) );
		}
		return panel( `Warnings et erreurs (${events?.length ?? "?"})`, "warnings", content );
	}

	private async loadSpectrum( viewer: SpectrumViewer, data: PageData ) {
		try {
			const res = await fetch( fileUrl( this.obsId, data.spectrum.file ), { cache: "no-store" } );
			if( !res.ok ) {
				throw new Error( `${res.status} ${res.statusText}` );
			}
			viewer.loadFits( await res.arrayBuffer( ) );
			viewer.setOverlay( data.spectrum.overlay );
		}
		catch( e ) {
			this.showMessage( `Spectre non chargé : ${e}` );
		}
	}

	/** The spectrum being validated and the most recent earlier ones of its
	 * star (BeSS and Rosace), searched in BeSS each time the page opens. */
	private async loadPrevious( viewer: SpectraCompareViewer, ewPlot: EwTimePlot, message: Label, legend: VBox ) {
		let result: PreviousSpectra;
		try {
			const res = await fetch( `${API}/bess/validations/${encodeURIComponent( this.obsId )}/previous`,
				{ cache: "no-store", signal: AbortSignal.timeout( 120000 ) } );
			if( !res.ok ) {
				const body = await res.json( ).catch( ( ) => ( {} ) );
				throw new Error( body.detail ?? `${res.status} ${res.statusText}` );
			}
			result = await res.json( ) as PreviousSpectra;
		}
		catch( e ) {
			message.setText( `Spectres précédents indisponibles : ${e}` );
			message.setClass( "warning", true );
			return;
		}

		const notes: string[] = [];
		if( result.found_by === "position" ) {
			notes.push( "Étoile non reconnue par son nom dans BeSS : spectres trouvés par position, à vérifier." );
		}
		else if( result.found_by === null && !result.bess_error ) {
			notes.push( "Aucun spectre de cette étoile dans BeSS." );
		}
		if( result.bess_error ) {
			notes.push( `BeSS : ${result.bess_error}` );
		}
		if( !result.spectra.length ) {
			notes.push( "Aucun spectre précédent." );
		}
		const unscaled = [ result.current, ...result.spectra ].filter( s => !s.scaled ).length;
		if( unscaled ) {
			notes.push( `${unscaled > 1 ? `${unscaled} spectres n'ont` : "Un spectre n'a"} pas pu être mis à la même échelle que les autres (voir la légende).` );
		}
		message.setText( notes.join( " " ) );
		message.setClass( "warning", result.found_by === "position" || !!result.bess_error || unscaled > 0 );
		message.show( notes.length > 0 );

		const ranks: Record<string, number> = {};
		const all = [ result.current, ...result.spectra ];
		const curves: CompareCurve[] = all.map( ( s, i ) => {
			const rank = ranks[s.source] = ( ranks[s.source] ?? 0 ) + 1;
			const color = s.source === "current" ? SOURCE_COLORS.current
				: `${SOURCE_COLORS[s.source]}-${( rank - 1 ) % COLOR_SHADES + 1}`;
			return { key: `${i}`, color, width: s.source === "current" ? 2 : 1.1,
				wavelength: s.wavelength, flux: s.flux, degraded: s.degraded };
		} );
		viewer.setCurves( curves );
		ewPlot.setPoints( all.flatMap( ( s, i ) => s.halpha_ew !== null && s.date_obs
			? [ { key: curves[i].key, date: Date.parse( s.date_obs.endsWith( "Z" ) || /[+-]\d\d:\d\d$/.test( s.date_obs ) ? s.date_obs : `${s.date_obs}Z` ),
				ew: s.halpha_ew, color: curves[i].color, current: s.source === "current" } ]
			: [] ) );

		legend.setContent( all.map( ( s, i ) => {
			const position = result.found_by === "position" && ( s.source === "bess" || s.source === "bess_rosace" )
				? ` — ⚠ par coordonnées (BeSS : ${s.bess_target_name || "?"})` : "";
			const scale = s.scaled ? "" : " — ⚠ pas à la même échelle (continuum 6510–6530 / 6595–6615 Å non couvert)";
			const text = [ utText( s.date_obs ), s.observer ?? "observateur inconnu", SOURCE_LABEL[s.source],
				s.resolving_power ? `R ${s.resolving_power}` : null, s.instrument,
				s.halpha_ew !== null ? `LE Hα ${s.halpha_ew.toFixed( 2 )} Å` : "LE Hα non mesurable" ].filter( x => x ).join( " — " ) + position + scale;
			const swatch = new Label( { cls: "swatch" } );
			swatch.setStyleValue( "background", `var(${curves[i].color})` );
			return new HBox( { cls: `legend-line${position || scale ? " suspect" : ""}`, content: [
				swatch,
				new Checkbox( { label: text, checked: true, change: ( ev ) => {
					viewer.setVisible( curves[i].key, !!ev.value );
					ewPlot.setVisible( curves[i].key, !!ev.value );
				} } ),
			] } );
		} ) );
	}

	/** An image of the page (guide image, 2D frame) with its overlay. */
	private async loadImage( viewer: FitsViewer, image: { file: string, overlay: ImageOverlay }, kind: string, what: string ) {
		try {
			const res = await fetch( fileUrl( this.obsId, image.file ), { cache: "no-store" } );
			if( !res.ok ) {
				throw new Error( `${res.status} ${res.statusText}` );
			}
			viewer.loadFits( await res.arrayBuffer( ), kind );
			viewer.setOverlay( image.overlay );

			// The panel may grow once what is beside it has loaded: fit the image
			// again whenever its frame changes size, until the observer zooms or pans it
			let touched = false;
			viewer.dom.addEventListener( "pointerdown", ( ) => { touched = true; } );
			viewer.dom.addEventListener( "wheel", ( ev: WheelEvent ) => { touched ||= ev.ctrlKey; } );
			new ResizeObserver( ( ) => {
				if( !touched ) {
					viewer.zoomFit( );
				}
			} ).observe( viewer.dom );
		}
		catch( e ) {
			this.showMessage( `${what} non chargée : ${e}` );
		}
	}

	/** Decision buttons active until the spectrum is sent; "Remettre à valider"
	 * only once decided. Returns whether a decision can still be taken. */
	private enableButtons( ): boolean {
		const decidable = this.status === "pending" || this.status === "accepted" || this.status === "rejected";
		this.buttons.forEach( b => b.enable( decidable ) );
		this.reset.enable( this.status === "accepted" || this.status === "rejected" );
		return decidable;
	}

	private async decide( decision: "accepted" | "rejected" | "pending" ) {
		this.buttons.forEach( b => b.disable( ) );
		try {
			const res = await fetch( `${API}/bess/validations/${encodeURIComponent( this.obsId )}/decision`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify( { decision, comment: this.comment.getValue( ).trim( ) || null } ),
				signal: AbortSignal.timeout( 10000 ),
			} );
			if( res.status === 401 ) {
				( this.props as ViewProps ).onUnauthorized( );
				return;
			}
			if( !res.ok ) {
				const body = await res.json( ).catch( ( ) => ( {} ) );
				throw new Error( body.detail ?? `${res.status} ${res.statusText}` );
			}
			this.back( );
		}
		catch( e ) {
			this.showMessage( `Décision non enregistrée : ${e}` );
			this.enableButtons( );
		}
	}
}

/** Root view: the login form, the list, or one spectrum, by the address (#<obs_id>). */

class Shell extends VBox {

	private loggedIn = false;

	constructor( props: BoxProps ) {
		super( props );
		window.addEventListener( "hashchange", ( ) => {
			if( this.loggedIn ) {
				this.route( );
			}
		} );
	}

	showLogin( ) {
		this.loggedIn = false;
		document.title = PAGE_TITLE;
		this.setContent( [ new LoginView( { flex: 1, onSuccess: ( ) => { this.loggedIn = true; this.route( ); } } ) ] );
	}

	route( ) {
		// x4js closes a tooltip only on its element's mouseleave, which an
		// element removed from the page never sends: the tooltip of the
		// clicked « Ouvrir » would stay on the next view. Sent here instead.
		this.dom.querySelectorAll( "[tooltip]" ).forEach( el => el.dispatchEvent( new MouseEvent( "mouseleave" ) ) );
		this.loggedIn = true;
		const obsId = decodeURIComponent( location.hash.replace( /^#/, "" ) );
		const onUnauthorized = ( ) => this.showLogin( );
		document.title = PAGE_TITLE;
		this.setContent( [ obsId
			? new SpectrumView( { flex: 1, obsId, onUnauthorized } )
			: new ListView( { flex: 1, onUnauthorized } ) ] );
	}
}

class BeValidationApp extends Application {

	constructor( ) {
		super( );

		initTooltips( );
		document.title = PAGE_TITLE;

		// Same "settings" entry as the main page: dark mode follows it.
		const settings = this.getStorageJSON( "settings" );
		if( settings?.darkMode ) {
			document.documentElement.setAttribute( "data-theme", "dark" );
		}

		const shell = new Shell( { cls: "mainview" } );
		this.setMainView( shell );

		fetchCurrentUser( ).then( user => {
			if( user ) {
				shell.route( );
			}
			else {
				shell.showLogin( );
			}
		} );
	}
}

window.onload = ( ) => {
	new BeValidationApp( );
}
