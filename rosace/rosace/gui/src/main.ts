import { VBox, HBox, Button, Label, Application, BoxProps, Image, Listbox, ListItem,
	SimpleText, SimpleTextArea, Notification, Tag, initTooltips, makeState, asap, EvMessage, HSizer,
	VSizer, Component, TextEdit, UnsafeHtml, unsafeHtml,
	} from "x4js";

import "./main.scss";

import { LoginView, fetchCurrentUser, logout as authLogout, UserOut } from "./auth";
import { FitsViewer, ImageOverlay } from "./widgets/fits_viewer";
import { SpectrumViewer, SpectrumOverlay } from "./widgets/spectrum_viewer";

import play_icon from "./assets/play.svg";
import stop_icon from "./assets/stop.svg";
import clock_icon from "./assets/clock.svg";
import seq_icon from "./assets/sequence.svg";
import red_icon from "./assets/flask.svg";
import check_icon from "./assets/circle-check.svg";
import error_icon from "./assets/circle-exclamation.svg";
import logout_icon from "./assets/logout.svg";
import moon_icon from "./assets/moon.svg";

import "@fontsource/montserrat/400.css"
import "@fontsource/montserrat/700.css"

// ::: Types ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

const STAGES  			= ["scheduler", "sequencer", "reduction", "sharing", "archive" ] as const;
type StageType = typeof STAGES[number]

// "scheduler" is never shown per-observation: an observation only appears once its target is
// known, i.e. once SCHEDULER has already completed — so its tag would always read "done".
// STAGES itself keeps "scheduler" (used elsewhere to recognize backend stage transitions).
const DISPLAY_STAGES 	= STAGES.filter( s => s !== "scheduler" );

// Toutes les valeurs possibles renvoyées par /status (backend OrchestratorStage)
type OrchestratorStageType = "idle" | "init_session" | StageType | "close_session" | "error";

const PIPELINE_STAGES 	= ["idle","init_session","observing","close_session"] as const;
type PipelineStageType = typeof PIPELINE_STAGES[number]

interface StageInfo {
    status: "pending" | "running" | "done" | "error";
    done_at: string | Date;
}

/**
 * returned by api server
 */

interface ObsRow {
    id: string;
    target_name: string;
    queried_name: string | null;
    program_name: string | null;
    priority: number | null;
    created_at: string | Date;
    error_message: string;
    stages: Record<string, StageInfo>;
}

/**
 * returned by GET /observations/steps -- see step_progress.py.
 * Keyed by observation id, then by stage ("sequencer"/"reduction") -- an
 * entry for a given stage exists once that stage has started for this
 * observation, and is kept (not cleared) once it finishes, so its line can
 * still be rendered as "all done" during the post-observation hold window
 * (see STEP_PROGRESS_HOLD_MS below).
 */

interface StepProgressEntry {
    step: string;
    steps: string[];
}

/**
 * returned by api server
 */

interface ApiStatus {
    stage: OrchestratorStageType;
    is_running: boolean;
    session_active: boolean;
    completed_count: number;
    pending_post_obs_count: number;
    scheduled_start: string | null;		// ISO UTC instant, null when nothing is scheduled
    scheduled_stop: string | null;
    site_longitude_deg: number | null;	// east positive, from the configuration (sidereal clock)
    hardware_test_running: boolean;		// the hardware test (POST /control/hardware-test) is running
    hardware_test_available: boolean;	// false in simulation (no hardware to test)
    demo_mode: boolean;					// demo configuration: shown in the title
}

type ScheduleKind = "start" | "stop";

/**
 * application data
 * shoud be exported & in a specific file
 */

interface AppData {
	connected: boolean;
	status: ApiStatus;
	observations: ObsRow[];
	step_progress: Record<string, Record<string, StepProgressEntry>>;
	settings: {	//saved locally
		darkMode?: boolean;
	}
};

const app_data = makeState<AppData>( {
	status: { stage: "idle", is_running: false, session_active: false, completed_count: 0, pending_post_obs_count: 0,
		scheduled_start: null, scheduled_stop: null, site_longitude_deg: null,
		hardware_test_running: false, hardware_test_available: false, demo_mode: false },
	connected: false,
	observations: [],
	step_progress: {},
	settings: {
	}
} );


// :: Debug  ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::


const __debug = true;

const FAKE_OBS = [
	{ id: "1", target_name: "Altair", queried_name: "Altair", priority: 1, created_at: new Date(), error_message: "", stages: {
		"scheduler": 	{ status: "done", done_at: new Date() },
		"sequencer": 	{ status: "running", done_at: new Date() },
//		"reduction":   	{ status: "done", done_at: new Date() },
		"sharing": 		{ status: "error", done_at: "" },
		"archive": 		{ status: "pending", done_at: new Date() }, }
	}
];

const FAKE_STATUS: ApiStatus = {
	is_running: true,
	session_active: true,
	completed_count: 1,
	pending_post_obs_count: 5,
	stage: "idle",
	scheduled_start: null,
	scheduled_stop: null,
	site_longitude_deg: null,
	hardware_test_running: false,
	hardware_test_available: true,
	demo_mode: false,
}



// ::: Constants ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

const API = "";

const OBS_FETCH_TIME = 3000;
const STATUS_FETCH_TIME = 5000;
const WS_RETRY_DELAY = 5000;

// Height of the "Spectre 1D" panel set with its divider, remembered by the browser.
const SPECTRUM_HEIGHT_STORAGE_KEY = "rosace.spectrum_height";

// Backend panel names (image_broadcast.py, GET /images/{panel})
const SPECTRUM_PANEL = "spectre1d";

// How long the SEQUENCER/REDUCTION step-progress lines stay visible, showing
// their last state ("all done"), after the whole observation finishes
// (ARCHIVE's own done_at) -- purely a display grace period, not tied to
// anything server-side; step_progress.py itself never expires an entry.
const STEP_PROGRESS_HOLD_SECONDS = 10;

// Hardware test: how long the button stays green or red, and the longest the
// test may take (devices are connected one at a time, ~5 s each, plus 3 s held).
const HW_TEST_RESULT_MS = 5000;
const HW_TEST_TIMEOUT_MS = 120000;

// In-browser Journal history cap -- not a real retention limit, rosace.log keeps everything.
const MAX_JOURNAL_LINES = 1000;

// Sticky-scroll tolerance (px) for the Journal panel: how close to the bottom counts
// as "already following" -- a couple of pixels of rounding slack, not a real margin.
const JOURNAL_AUTOSCROLL_TOLERANCE_PX = 4;

const LOG_CREATE_MSG = "log.create";
const IMAGE_NEW_MSG = "image.new";
const AUTH_LOGOUT_MSG = "auth.logout";	// fired locally (Application.fireGlobal) on logout — AppShell listens

// Sub-plugin display names (GET /subplugins/display-names), fetched once at
// startup -- static for the server's lifetime, unlike app_data's polled
// fields, so plain module state rather than an app_data/watch()-tracked
// field is enough: nothing needs to re-render when it arrives, renderObs()
// just reads it directly whenever a step-progress row is built.
let subplugin_display_names: Record<string, Record<string, string>> = {};

const STAGE_LABEL: Record<StageType, { text: string, icon: string }> = {	//allow only a value of stages in stage_label
    scheduler: { text: "SCHEDULER", icon: null },
    sequencer: { text: "SEQUENCER", icon: null },
    reduction: { text: "REDUCTION", icon: null },
    sharing:   { text: "SHARING", icon: null },
    archive:   { text: "ARCHIVE", icon: null },
};




const PIPELINE_LABEL: Record<typeof PIPELINE_STAGES[number], string> = {	//allow only a value of pipeline_stages in pipeline_label
    idle:          "IDLE",
    init_session:  "INIT",
    observing:     "OBSERVING",
    close_session: "CLOSE",
 };

/**
 * Application Header
 */

const APP_TITLE = "Rosace — Robotic Spectro";

class AppHeader extends HBox {

	private demo_badge: Label;
	private hwtest: Button;
	private hw_busy = false;				// this page's own test is running
	private hw_showing_result = false;		// green or red, for HW_TEST_RESULT_MS

	constructor( props: BoxProps ) {
		super( props );

        const chips = PIPELINE_STAGES.map( s => {
            return new Label({ 
				cls: "chip", 
				id: "chip-"+s, 
				text: PIPELINE_LABEL[s],
			})
        });
        
		let label;
		this.setContent( [
                new HBox({ cls: "header-logo", content: [
					new Image( { id: "image", src: "assets/logo.svg" } ), 
					new Label( { id: "title", text: APP_TITLE }),
					this.demo_badge = new Label( { id: "demo-badge", text: "DEMO MODE" }),
				] }),
                new HBox({ cls: "pipeline-bar", content: chips }),
                label = new Label({ cls: "obs-count-badge", text: "0 obs" }),
				this.hwtest = new Button( { id: "hwtest", label: "Test", click: ( ) => this.runHardwareTest( ) } ),
				new Button( { id: "darkmode", icon: moon_icon, tooltip: "Réglages", menu: [
					{ text: "Dark mode", checked: ( ) => app_data.settings.darkMode, click: ( ) => { app_data.settings.darkMode = !app_data.settings.darkMode; } },
				]} ),
				new Button( { id: "logout", icon: logout_icon, tooltip: "Déconnexion", click: ( ) => { authLogout().then( ( ) => Application.fireGlobal( AUTH_LOGOUT_MSG ) ); } } ),
        ] );

		app_data.watch( "status.completed_count", e => {
			label.setText( `${e.value} obs` );	// update label
		})
		app_data.watch( "status", ( ) => { this.refreshHardwareTest( ); this.refreshTitle( ); } );
		this.refreshHardwareTest( );
		this.refreshTitle( );
	}

	/**
	 * A red "DEMO MODE" badge next to the title, and "DEMO mode" in the
	 * browser tab (plain text only there), when the server runs the demo
	 * configuration (status.demo_mode).
	 */
	private refreshTitle( ) {
		const demo = app_data.status.demo_mode;
		this.demo_badge.show( demo );
		document.title = demo ? `${APP_TITLE} — DEMO mode` : APP_TITLE;
	}

	/**
	 * Greyed during a session, in simulation and while a test runs (this page's
	 * or another one's); left alone while the green/red result is shown.
	 */
	private refreshHardwareTest( ) {
		if( this.hw_showing_result ) {
			return;
		}
		const status = app_data.status;
		const running = this.hw_busy || status.hardware_test_running;
		let tip = "Tester la connexion au matériel (INDI, PHD2)";
		if( !status.hardware_test_available ) {
			tip = "Pas de matériel à tester (simulation)";
		}
		else if( status.session_active ) {
			tip = "Session en cours";
		}
		this.hwtest.setText( running ? "Test…" : "Test" );
		this.hwtest.setAttribute( "tooltip", tip );
		this.hwtest.enable( status.hardware_test_available && !status.session_active && !running );
	}

	/**
	 * Connects to every device and PHD2 (server side), then shows the result:
	 * green or red for HW_TEST_RESULT_MS, the failures in the tooltip. The
	 * details also reach the Journal, as [HW_TEST] log lines.
	 */
	private async runHardwareTest( ) {
		this.hw_busy = true;
		this.refreshHardwareTest( );
		let ok = false;
		let tip: string;
		try {
			const res = await fetch( `${API}/control/hardware-test`, { method: "POST", signal: AbortSignal.timeout( HW_TEST_TIMEOUT_MS ) } );
			if( res.status === 401 ) {
				this.hw_busy = false;
				Application.fireGlobal( AUTH_LOGOUT_MSG );
				return;
			}
			const data = await res.json( );
			if( res.ok ) {
				ok = data.ok;
				tip = ok ? "Matériel OK" : ( data.errors as string[] ).join( "\n" );
			}
			else {
				tip = data.detail ?? res.statusText;
			}
		}
		catch( e ) {
			tip = `Erreur : ${e}`;
		}
		this.hw_busy = false;
		this.hw_showing_result = true;
		const cls = ok ? "ok" : "fail";
		this.hwtest.addClass( cls );
		this.hwtest.setText( "Test" );
		this.hwtest.setAttribute( "tooltip", tip );
		this.hwtest.disable( );
		this.setTimeout( "hwtest", HW_TEST_RESULT_MS, ( ) => {
			this.hwtest.removeClass( cls );
			this.hw_showing_result = false;
			this.refreshHardwareTest( );
		} );
	}
}

/**
 * Local sidereal time ("18:42:07") at a longitude (degrees, east positive) —
 * same low-precision Meeus GMST polynomial as astro_math.py's
 * _greenwich_mean_sidereal_time_deg(), a display only (accurate to well under
 * a second).
 */

function formatSiderealTime( now: Date, longitude_deg: number ) {
	const d = now.getTime( ) / 86400000 + 2440587.5 - 2451545.0;	// days since J2000.0
	const t = d / 36525;
	const gmst = 280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - t * t * t / 38710000;
	const lst_deg = ( ( gmst + longitude_deg ) % 360 + 360 ) % 360;
	const total = Math.floor( lst_deg / 15 * 3600 );
	const two = ( n: number ) => String( n ).padStart( 2, "0" );
	return `${two( Math.floor( total / 3600 ) )}:${two( Math.floor( total / 60 ) % 60 )}:${two( total % 60 )}`;
}

/**
 * Application Footer — UTC clock on the left, local sidereal time in the
 * middle, browser-local clock on the right, all ticking every second. All three
 * deliberately share the exact same formatting rule (24h, no AM/PM) as the rest
 * of the app's time displays (see logMany()/renderObs() above) — the whole
 * point of showing them side by side is to let the observer compare them at a
 * glance, which only works if they're in the same format. The sidereal time
 * needs the site's longitude, read from /status (the configuration's): until
 * the first /status answer it shows "--:--:--".
 */

class AppFooter extends HBox {

	constructor( props: BoxProps ) {
		super( { cls: "app-footer", ...props } );

		const formatUtc   = ( now: Date ) => "UTC   " + now.toLocaleTimeString( undefined, { timeZone: "UTC", hour12: false } );
		const formatLocal = ( now: Date ) => "Heure locale " + now.toLocaleTimeString( undefined, { hour12: false } );
		const formatLst   = ( now: Date ) => {
			const longitude = app_data.status.site_longitude_deg;
			return "Heure sidérale locale (TSL) " + ( longitude === null || longitude === undefined ? "--:--:--" : formatSiderealTime( now, longitude ) );
		};

		const utcLabel   = new Label( { cls: "clock utc-clock",   text: formatUtc( new Date( ) ) } );
		const lstLabel   = new Label( { cls: "clock lst-clock",   text: formatLst( new Date( ) ) } );
		const localLabel = new Label( { cls: "clock local-clock", text: formatLocal( new Date( ) ) } );
		this.setContent( [ utcLabel, lstLabel, localLabel ] );

		// named/repeating timer (x4js CoreElement) — same idiom as fetchStatus()/
		// fetchObs()'s own polling below; holds only a WeakRef on this component,
		// so it self-clears once this AppFooter is no longer reachable, with no
		// dispose hook needed.
		this.setInterval( "clock", 1000, ( ) => {
			const now = new Date( );
			utcLabel.setText( formatUtc( now ) );
			lstLabel.setText( formatLst( now ) );
			localLabel.setText( formatLocal( now ) );
		} );
	}
}

/**
 * MainView is the main dashboard view
 */

/** A panel's title bar: its name (in capitals and the accent colour, like the
 * other panels' titles), then the parts given (object, observation ID) in their
 * own case and the text colour, " — " between them. */

function panelTitle( name: string, ...parts: ( string | undefined )[] ): UnsafeHtml {
	const detail = parts.map( p => p?.trim( ) ).filter( p => p ).join( " — " );
	return unsafeHtml( detail ? `${name}<span class="title-detail">${escapeText( detail )}</span>` : name );
}

/** Plain text made safe for inclusion in HTML (a star name could contain "<" or "&"). */

function escapeText( text: string ): string {
	return text.replace( /&/g, "&amp;" ).replace( /</g, "&lt;" ).replace( />/g, "&gt;" );
}

class MainView extends VBox {

	private obs_list: Listbox;
	private log_lines: string[];
	private last_stage: OrchestratorStageType;
	private spectre_viewer: FitsViewer;
	private guidage_viewer: FitsViewer;
	private spectrum_panel: VBox;
	private spectrum_viewer: SpectrumViewer;
	// Panel title bars: "<panel> — <object> — <observation ID>" of the image shown
	private guidage_title: Label;
	private spectre_title: Label;
	private spectrum_title: Label;

	// Scheduled start/stop (controls bar): one time field per button, one chip per
	// scheduled instant (shown only while something is scheduled). Times are typed
	// in local time (what the browser's time picker offers); the server converts
	// them to UTC.
	private start_button: Button;
	private stop_button: Button;
	private start_time: TextEdit;
	private stop_time: TextEdit;
	private schedule_chips = {} as Record<ScheduleKind, { box: HBox, label: Label }>;
	// "Arrêt demandé": a stop was asked, the current observation goes on to its end
	private stop_notice: Label;

	constructor( props: BoxProps ) {
		super( props );

		this.log_lines = [];

		this.setContent( [
			new AppHeader( { } ),

			new HBox( { cls: "main-split", flex: 1, content: [
				new VBox( { cls: "left-col", flex: 1, content: [
					//<todo: x4btngroup
					new HBox( { cls: "controls-bar", content: [
						this.start_button = new Button({ label: "START", icon: play_icon, id: "start", click: () => this.controlStart() }),
						this.start_time = new TextEdit({ cls: "schedule-time", label: "", type: "time", value: "",
							tooltip: "Heure de départ (vide : départ immédiat)", change: () => this.updateControls() }),
						new HBox({ cls: "schedule-chips", flex: 1, content: [
							this.makeScheduleChip( "start" ),
							this.stop_notice = new Label({ cls: "stop-notice", text: "" }),
							this.makeScheduleChip( "stop" ),
						] }),
						this.stop_time = new TextEdit({ cls: "schedule-time", label: "", type: "time", value: "",
							tooltip: "Heure d'arrêt (vide : arrêt immédiat)", change: () => this.updateControls() }),
						this.stop_button = new Button({ label: "STOP", icon: stop_icon, id: "stop",  click: () => this.controlStop() }),
					]} ),
					//<todo: use x4panel
					new VBox({ cls: "panel obs", flex: 1, content: [
						new Label({ cls: "title", text: "Observations" }),
						this.obs_list = new Listbox({ cls: "obs-list", flex: 1, items: [] }),
					] }),
					//<todo: use x4panel
					new VBox({ cls: "panel log", flex: 1, content: [
						new Label({ cls: "title", text: "Journal" }),
						new SimpleTextArea({ cls: "log-box", id: "logger", flex: 1, readonly: true, resize: false }),
					]}),

				]}),
				new HSizer(),
				// Spectre 1D on top (fed by the reduction only), a divider to share the
				// height, then the two camera images side by side
				new VBox({ cls: "right-col", flex: 1, content: [
					this.spectrum_panel = new VBox({ cls: "panel spectre1d", flex: 1, content: [
						this.spectrum_title = new Label({ cls: "title keep-case", text: "SPECTRE 1D" }),
						this.spectrum_viewer = new SpectrumViewer({ flex: 1 }),
					] }),
					this.makeSpectrumSizer( ),
					new HBox({ cls: "images-row", flex: 1, content: [
						new VBox({ cls: "panel guidage", flex: 1, content: [
							this.guidage_title = new Label({ cls: "title keep-case", text: "GUIDAGE" }),
							this.guidage_viewer = new FitsViewer({ flex: 1 }),
						] }),
						new VBox({ cls: "panel spectre", flex: 1, content: [
							this.spectre_title = new Label({ cls: "title keep-case", text: "SPECTRE 2D" }),
							this.spectre_viewer = new FitsViewer({ flex: 1 }),
						] }),
					] }),
				] })
			]}),

			new AppFooter( { } ),
		]);

		this.log( "application started" )

		this.setInterval( "schedule", 1000, ( ) => this.renderScheduleChips( ) );

		// backend log records, pushed over the WebSocket log channel in periodic batches
		this.onGlobalEvent( ( ev: EvMessage ) => {
			if( ev.msg === LOG_CREATE_MSG ) {
				const lines = ev.params.lines as { level: string, text: string }[];
				this.logMany( lines.map( l => `[${l.level}] ${l.text}` ) );
			}
			else if( ev.msg === IMAGE_NEW_MSG ) {
				const { panel, overlay, kind } = ev.params as { panel: string, overlay?: ImageOverlay, kind?: string };
				this.loadPanelImage( panel, overlay ?? null, kind );
			}
		} );

		// watch observations
		app_data.watch( "observations", e => {
			this.renderObs( e.value );
		})

		// watch status
		app_data.watch( "status", e => {
			this.updateStageChips( e.value );
			this.refreshStopNotice( e.value );
		} );
			
		// attendons d'etre montés dans l'UI
		asap( () => {
			this.fetchStatus();
			this.fetchObs();
			this.loadLastImages();
			this.fetchDisplayNames();
			this.restoreSpectrumHeight();
			this.loadSpectrum( null, true );
		});
    }

	/**
	 * At startup, shows the last image each 2D panel received (its preview file
	 * survives a server restart), with its overlay when the server kept one --
	 * a panel that never received any image stays empty.
	 */

	private async loadLastImages() {
		for( const panel of [ "spectre", "guidage" ] ) {
			try {
				const res = await fetch( `${API}/images/${panel}/overlay`, { cache: "no-store" } );
				const overlay = res.ok ? await res.json() as ImageOverlay | null : null;
				await this.loadPanelImage( panel, overlay );
			}
			catch( e ) {
				this.showError( `${panel} image load error`, e.toString() );
			}
		}
	}

	/**
	 * Fetched once, at startup -- see subplugin_display_names' own comment above.
	 */

	private async fetchDisplayNames() {
		try {
			const res = await fetch( `${API}/subplugins/display-names`, { signal: AbortSignal.timeout(5000) } );
			if( res.ok ) {
				subplugin_display_names = await res.json() as Record<string, Record<string, string>>;
			}
		}
		catch( e ) {
			this.showError( `display-names fetch error`, e.toString() );
		}
	}

	/**
	 * Fetches a panel's current preview image (pushed by the backend, see
	 * image_broadcast.py) and displays it with its overlay -- applied only once
	 * the new image is loaded, and null clears the previous image's one. panel
	 * is "guidage" or "spectre" (the 2D images); "spectre1d" goes to loadSpectrum().
	 * kind is the type of image sent by the server ("science", "dark", "guide",
	 * "slit"...): the viewer redoes its automatic stretch when it changes.
	 */

	private async loadPanelImage( panel: string, overlay: ImageOverlay | null, kind?: string ) {
		if( panel === SPECTRUM_PANEL ) {
			return this.loadSpectrum( overlay as SpectrumOverlay | null, false );
		}
		const viewer = panel === "guidage" ? this.guidage_viewer
		             : panel === "spectre" ? this.spectre_viewer
		             : undefined;
		if( !viewer ) {
			return;
		}

		const res = await fetch( `${API}/images/${panel}`, { cache: "no-store" } );
		if( !res.ok ) {
			return;
		}

		viewer.loadFits( await res.arrayBuffer(), kind );
		viewer.setOverlay( overlay );

		// the server stamps the object and observation ID on its preview copy
		const header = viewer.getHeader( ) ?? {};
		const title = panel === "guidage" ? this.guidage_title : this.spectre_title;
		title.setText( panelTitle( panel === "guidage" ? "GUIDAGE" : "SPECTRE 2D", header["OBJECT"], header["OBS_ID"] ) );
	}


	/**
	 * Fetches the last published 1D spectrum (see reduction_helpers.publish_spectrum_preview())
	 * and shows it with its overlay. At startup (fromStart), the overlay is read from the
	 * server, which keeps it next to the file, and a missing spectrum is not an error.
	 */

	private async loadSpectrum( overlay: SpectrumOverlay | null, fromStart: boolean ) {
		try {
			const res = await fetch( `${API}/images/${SPECTRUM_PANEL}`, { cache: "no-store" } );
			if( !res.ok ) {
				return;
			}
			const buffer = await res.arrayBuffer( );
			if( fromStart ) {
				const ov = await fetch( `${API}/images/${SPECTRUM_PANEL}/overlay`, { cache: "no-store" } );
				overlay = ov.ok ? await ov.json( ) as SpectrumOverlay | null : null;
			}
			this.spectrum_viewer.loadFits( buffer );
			this.spectrum_viewer.setOverlay( overlay );
			this.spectrum_title.setText( panelTitle( "SPECTRE 1D", overlay?.title ) );
		}
		catch( e ) {
			this.showError( "spectrum load error", e.toString( ) );
		}
	}

	/** Divider under the "Spectre 1D" panel: resizes it, and remembers its height. */

	private makeSpectrumSizer( ) {
		const sizer = new VSizer( false );	// resizes the panel above it
		sizer.on( "resize", ev => {
			try {
				localStorage.setItem( SPECTRUM_HEIGHT_STORAGE_KEY, String( Math.round( ev.height ) ) );
			}
			catch( e ) {
				// storage unavailable: the height just isn't remembered
			}
		} );
		return sizer;
	}

	private restoreSpectrumHeight( ) {
		let height = 0;
		try {
			height = parseInt( localStorage.getItem( SPECTRUM_HEIGHT_STORAGE_KEY ) ?? "" );
		}
		catch( e ) {
			// storage unavailable: default split
		}
		if( height > 0 ) {
			this.spectrum_panel.setStyleValue( "flex", `0 0 ${height}px` );
		}
	}

	/**
	 * render observations,
	 * trigged when application.data.observations change
	 */

	private renderObs(rows: ObsRow[]) {

        this.obs_list.setItems( rows.map( obs => {
			const time = new Date(obs.created_at).toLocaleTimeString(undefined, { timeZone: "UTC", hour12: false });

			const stages = DISPLAY_STAGES.map( (key) => {
				const info    = obs.stages[key];
				if( !info ) {
					return new Tag({
						label:    STAGE_LABEL[key].text,
						icon:     error_icon,
						cls:      `stage unknown`,
						tooltip:  `no information`,
					});
				}
				else {
					const doneAt  = info.done_at ? " @ " + new Date(info.done_at).toLocaleTimeString(undefined, { timeZone: "UTC", hour12: false }) : "";

					return new Tag({
						label:    STAGE_LABEL[key].text,
						icon:     info.done_at ? check_icon : clock_icon,
						cls:      `stage ${info.status}`,
						tooltip:  `${STAGE_LABEL[key].text}: ${info.status}${doneAt}`,
					});
				}
			});

			// Up to two extra, temporary lines under the primary stage row -- one for
			// SEQUENCER's own sub-plugin steps, one for REDUCTION's -- each independent
			// (step_progress.py keeps both stages' entries around, not just the current
			// one) so a finished SEQUENCER line can still show "all done" while REDUCTION
			// runs. Both stay visible until STEP_PROGRESS_HOLD_SECONDS after the whole
			// observation finishes (ARCHIVE's own done_at) -- purely a display grace
			// period, giving the observer time to see the full pipeline at a glance
			// before it collapses back to just the primary stage row.
			const obsSteps    = app_data.step_progress[obs.id] ?? {};
			const archiveInfo = obs.stages["archive"];
			const withinHold  = archiveInfo?.status === "done" && !!archiveInfo.done_at
				&& ( Date.now() - new Date( archiveInfo.done_at ).getTime() ) < STEP_PROGRESS_HOLD_SECONDS * 1000;

			const buildStepLine = ( stage: "sequencer" | "reduction" ): HBox | null => {
				const entry = obsSteps[stage];
				const info  = obs.stages[stage];
				if( !entry || !( info?.status === "running" || withinHold ) ) {
					return null;
				}

				const allDone      = info.status === "done";
				const currentIndex = entry.steps.indexOf( entry.step );
				const stepNames    = subplugin_display_names[stage] ?? {};
				const stepChips = entry.steps.map( (name, i) => {
					const label  = stepNames[name] ?? name;
					const status = allDone || i < currentIndex ? "done" : i === currentIndex ? "running" : "pending";
					return new Tag({
						label,
						cls:     `substep ${status}`,
						tooltip: `${label}: ${status}`,
					});
				} );
				return new HBox({ cls: "obs-substeps", content: stepChips });
			};

			const stagesBox = new HBox({ cls: "obs-stages", content: stages });
			const stepLines = [ buildStepLine("sequencer"), buildStepLine("reduction") ]
				.filter( (line): line is HBox => line !== null );
			const stagesColumn: Component = stepLines.length === 0
				? stagesBox
				: new VBox({ cls: "obs-stages-column", content: [ stagesBox, ...stepLines ] });

			// Name used to query the catalogue (e.g. "Vega"), shown in bold; the canonical
			// name (e.g. "* alf Lyr") follows in parentheses, only when it differs from it.
			// Both share a single "obs-name" column — the canonical span is just empty text
			// when there's nothing to add, so the column doesn't jump around.
			const displayName   = obs.queried_name || obs.target_name;
			const showCanonical = !!obs.queried_name && obs.queried_name !== obs.target_name;
			const canonicalText = showCanonical ? `(${obs.target_name})` : "";
			const priorityText  = obs.priority != null ? `P${obs.priority}` : "";

			const r: ListItem = {
				id: obs.id,
				text: null,
				cls: 'obs-row',
				sub_cols: [
					new HBox({ cls: "obs-name", content: [
						new SimpleText({ cls: "obs-target",    text: displayName }),
						new SimpleText({ cls: "obs-canonical", text: canonicalText }),
					] }),
					new SimpleText({ cls: "obs-program",   text: obs.program_name ?? "" }),
					new SimpleText({ cls: "obs-priority",  text: priorityText }),
					new SimpleText({ cls: "obs-id",        text: obs.id }),
					new SimpleText({ cls: "obs-time",      text: time }),
					stagesColumn,
				]
			}

			return r;
		}) );
    }

	/**
	 * 
	 */

	private updateStageChips( status: ApiStatus ) {
        let activeKey: string;
        
		if (status.stage === "init_session")   {
			activeKey = "init_session";
		}
        else if ( (STAGES as readonly string[]).includes( status.stage ) )  {
			activeKey = "observing";
		}
        else if (status.stage === "close_session")  {
			activeKey = "close_session";
		}
        else                                 {
			activeKey = "idle";
		}

        PIPELINE_STAGES.forEach( s => {
            const chip = this.query<Label>( "#chip-"+s )
            chip.removeClass("dim active error");
            if (s === activeKey) {
				//<review, pourquoi error, c'est une valeur retournée possible ?
				// dans ce cas l'ajouter aux valeurs acceptées
                chip.addClass(status.stage === "error" ? "error" : "active");
            } 
			else {
                chip.addClass("dim");
            }
        });
    }

	/**
	 * appends several lines in one shot: a single setText() for the whole batch,
	 * so a burst of incoming messages doesn't trigger one DOM redraw per line
	 */

	private logMany(msgs: string[]) {
        // UTC to match rosace.log and everything else timestamp-related in the app
        // (DB rows, FITS filenames, the astronomical-day boundary); locale left undefined
        // (browser's own preference) rather than hardcoded, since the app runs anywhere.
        // hour12: false avoids AM/PM ambiguity regardless of the browser's locale.
        const ts = new Date().toLocaleTimeString(undefined, { timeZone: "UTC", hour12: false });

        for( const msg of msgs ) {
            this.log_lines.push(`[${ts}] ${msg}`);
        }

        if (this.log_lines.length > MAX_JOURNAL_LINES) {
			this.log_lines.splice( 0, this.log_lines.length - MAX_JOURNAL_LINES );
		}

		const l = this.query<SimpleTextArea>( "#logger" );
		const el = l.dom as HTMLTextAreaElement;

		// Sticky auto-scroll: only follow the new lines down if the user was already
		// at (or very near) the bottom -- if they've scrolled up to read earlier
		// lines, setText() below must not yank them back down on every new batch.
		const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= JOURNAL_AUTOSCROLL_TOLERANCE_PX;

        l.setText( this.log_lines.join('\n' ) );

		if( wasAtBottom ) {
			l.scrollToBottom();
		}
    }

	private log(msg: string) {
        this.logMany( [ msg ] );
    }

	private async fetchStatus() {
        try {
            const res = await fetch(`${API}/status`, { signal: AbortSignal.timeout(5000) });
            if (res.status === 401) {
				// session gone (logout, expiry) — stop polling instead of spamming 401s
				// forever in the background; AppShell switches back to the login screen.
				Application.fireGlobal( AUTH_LOGOUT_MSG );
				return;
			}
            if (!res.ok) {
				throw new Error( res.statusText );
			}

            const data = await res.json() as ApiStatus;
			if( !app_data.connected ) {
				app_data.connected = true;
                this.log(`API connected — ${data.is_running ? "session running" : "idle"}`);
            }

			app_data.status = data;

			//this.obsCountBadge.setText(`${d.completed_count} obs`);
			this.updateControls();
			this.renderScheduleChips();

			if (data.stage !== this.last_stage) {
				if (this.last_stage !== null) {
					let label;
					if( (PIPELINE_STAGES as readonly string[]).includes( data.stage ) ) {
						label = PIPELINE_LABEL[data.stage as PipelineStageType];
					}
					else {
						label = data.stage.toUpperCase()
					}

					this.log(`Stage → ${label}`);
				}

				this.last_stage = data.stage;
			}
		}
		catch( e ) {
			this.showError( `status fetch error`, e.toString() );
            if (app_data.connected) {
                app_data.connected = false;
                this.log("API disconnected");
            }
        }
		// not in a finally: the 401 branch above returns early, deliberately skipping
		// this reschedule — see its comment.
		this.setTimeout( "status", STATUS_FETCH_TIME, ( ) => this.fetchStatus() );
    }

	private async fetchObs() {
        try {
			const res = await fetch(`${API}/observations/recent`, { signal: AbortSignal.timeout(5000) });
			if (res.status === 401) {
				Application.fireGlobal( AUTH_LOGOUT_MSG );
				return;
			}
			if (!res.ok) {
				throw new Error( res.statusText );
			}

			const obs = await res.json() as ObsRow[];

			// Fetched alongside observations, same tick, best-effort: a failure here
			// shouldn't stop the observation list itself from updating -- it just
			// means the step-progress second line stays stale/empty this poll.
			try {
				const stepsRes = await fetch( `${API}/observations/steps`, { signal: AbortSignal.timeout(5000) } );
				if( stepsRes.ok ) {
					// Assigned before app_data.observations below -- renderObs() (triggered
					// by the "observations" watch) reads app_data.step_progress directly at
					// render time, so it must already be current by then.
					app_data.step_progress = await stepsRes.json() as Record<string, Record<string, StepProgressEntry>>;
				}
			}
			catch( e ) {
				this.showError( `step-progress fetch error`, e.toString() );
			}

			//this.renderObs( obs );
			app_data.observations = obs;
        }
		catch( e ) {
			this.showError( `obj fetch error`, e.toString() );
		}
		// not in a finally: the 401 branch above returns early, deliberately skipping
		// this reschedule — see fetchStatus()'s matching comment.
		this.setTimeout( "obs", OBS_FETCH_TIME, ( ) => this.fetchObs() );
    }

	private async controlStart() {
		if( this.start_time.getValue() ) {
			return this.scheduleControl( "start" );
		}
        const bt = this.query("#start")
		
    	bt.disable();
        try {
	        const res  = await fetch(`${API}/control/start`, { method: "POST", signal: AbortSignal.timeout(5000) });
			if( res.ok ) {
				const data = await res.json() as { message: string };
				this.log(`START → ${data.message}`);
				this.showMsg( "start", data.message );
				this.fetchStatus();
			}
			else {
				this.showError( `network error ${res.status}`, res.statusText );
				this.log(`start error (${res.status}) ${res.statusText }` );
			}
        } 
		catch (e) {
			this.showError( `fetch error`, e.toString() );
		}
		finally {
            this.updateControls( );
        }
    }

    private async controlStop() {
		if( this.stop_time.getValue() ) {
			return this.scheduleControl( "stop" );
		}
        const bt = this.query("#stop")
		bt.disable();

        try {
            const res  = await fetch(`${API}/control/stop`, { method: "POST", signal: AbortSignal.timeout(5000) });
			if( res.ok ) {
				const data = await res.json() as { message: string };
				this.log(`STOP → ${data.message}`);
				this.showMsg( "stop", data.message);
				this.fetchStatus();
			}
			else {
				this.showError( `network error ${res.status}`, res.statusText );
			}
        } 
		catch (e) {
			this.showError( `internal error`, "" );
            this.log(`Erreur STOP : ${e}`);
        }
		finally {
            this.updateControls( );
        }
    }

	/**
	 * Shown while a stop was asked but the session still runs: the current
	 * observation goes on to its end, then CLOSE_SESSION -- a long series can
	 * take half an hour, with little else changing on screen.
	 */
	private refreshStopNotice( status: ApiStatus ) {
		const stopping = status.session_active && !status.is_running;
		this.stop_notice.show( stopping );
		this.stop_notice.setText( "Arrêt demandé" );
	}

	// ::: Scheduled start / stop :::::::::::::::::::::::::::::::::::::::::::::::

	/**
	 * Buttons follow their time field: filled, the button schedules ("START 21:20");
	 * empty, it acts now. A filled field also enables its button, so a stop can be
	 * scheduled before the session has started.
	 */
	private updateControls( ) {
		const status = app_data.status;
		const start = this.start_time.getValue( );
		const stop = this.stop_time.getValue( );
		this.start_button.setText( start ? `START ${start}` : "START" );
		this.stop_button.setText( stop ? `STOP ${stop}` : "STOP" );
		this.start_button.enable( ( !status.session_active && !status.hardware_test_running ) || !!start );
		this.stop_button.enable( status.is_running || !!stop );
	}

	private async scheduleControl( kind: ScheduleKind ) {
		const field = kind === "start" ? this.start_time : this.stop_time;
		const time = field.getValue( );
		try {
			const res = await fetch( `${API}/control/schedule/${kind}`, {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify( { time } ),
				signal: AbortSignal.timeout( 5000 ),
			} );
			if( res.ok ) {
				const data = await res.json( ) as Record<string, string>;
				const text = `${kind === "start" ? "Départ" : "Arrêt"} planifié : ${this.formatScheduleTime( new Date( data[`scheduled_${kind}`] ) )}`;
				this.log( text );
				this.showMsg( kind, text );
				field.setValue( "" );
				this.fetchStatus( );
			}
			else {
				this.showError( `schedule error ${res.status}`, res.statusText );
			}
		}
		catch( e ) {
			this.showError( `fetch error`, e.toString() );
		}
		finally {
			this.updateControls( );
		}
	}

	private async cancelSchedule( kind: ScheduleKind ) {
		try {
			const res = await fetch( `${API}/control/schedule/${kind}`, { method: "DELETE", signal: AbortSignal.timeout( 5000 ) } );
			if( res.ok ) {
				this.log( `${kind === "start" ? "Départ" : "Arrêt"} planifié annulé` );
				this.fetchStatus( );
			}
			else {
				this.showError( `cancel error ${res.status}`, res.statusText );
			}
		}
		catch( e ) {
			this.showError( `fetch error`, e.toString() );
		}
	}

	/** One chip per kind, built once and only shown while that kind is scheduled. */
	private makeScheduleChip( kind: ScheduleKind ) {
		const label = new Label( { cls: "schedule-text", text: "" } );
		const box = new HBox( { cls: `schedule-chip ${kind}`, content: [
			label,
			new Button( { cls: "schedule-cancel", label: "✕", tooltip: "Annuler", click: ( ) => this.cancelSchedule( kind ) } ),
		] } );
		box.hide( );
		this.schedule_chips[kind] = { box, label };
		return box;
	}

	/** Called every second (countdown) and after each /status poll. */
	private renderScheduleChips( ) {
		const now = Date.now( );
		for( const kind of ["start", "stop"] as ScheduleKind[] ) {
			const iso = kind === "start" ? app_data.status.scheduled_start : app_data.status.scheduled_stop;
			const chip = this.schedule_chips[kind];
			if( !iso ) {
				chip.box.hide( );
				continue;
			}
			const at = new Date( iso );
			chip.label.setText( `${this.formatScheduleTime( at )} · dans ${this.formatCountdown( at.getTime( ) - now )}` );
			chip.box.show( );
		}
	}

	/** "21:20", "dim. 05:30" when not today — local time. */
	private formatScheduleTime( at: Date ) {
		const time = at.toLocaleTimeString( undefined, { hour: "2-digit", minute: "2-digit", hour12: false } );
		const prefix = at.toDateString( ) === new Date( ).toDateString( ) ? "" : at.toLocaleDateString( undefined, { weekday: "short" } ) + " ";
		return prefix + time;
	}

	/** "2 h 14", "12 min", "45 s" (seconds only in the last minute). */
	private formatCountdown( ms: number ) {
		const seconds = Math.max( 0, Math.round( ms / 1000 ) );
		if( seconds < 60 ) {
			return `${seconds} s`;
		}
		const minutes = Math.ceil( seconds / 60 );
		if( minutes < 60 ) {
			return `${minutes} min`;
		}
		return `${Math.floor( minutes / 60 )} h ${String( minutes % 60 ).padStart( 2, "0" )}`;
	}

	private showMsg( title: string, text: string) {
        new Notification( {title,text,}).display( 5 );
    }

	private showError( title: string, text: string) {
		new Notification( {title,text,mode:"danger"}).display( 5 );
	}
}

interface AppShellProps extends BoxProps {
	onLoginSuccess: ( user: UserOut ) => void;
}

/**
 * Persistent root view (the one and only setMainView() call — x4js's Application
 * doesn't support swapping it, see connectWebSocket()'s caller for why this
 * matters) that swaps its own content between LoginView and MainView, instead.
 * MainView is created lazily, on first successful login/boot — its constructor
 * already starts polling /status and /observations/recent, so building it any
 * earlier would mean firing those (401, unauthenticated) from the login screen.
 * A fresh MainView is built on every showMain() call rather than cached/reused:
 * fetchStatus()/fetchObs() permanently stop rescheduling themselves once they hit
 * a 401 (see their own comments) — reusing the same instance after a logout would
 * mean its polling loop never resumes on the next login.
 */

class AppShell extends VBox {

	constructor( props: AppShellProps ) {
		super( { cls: "mainview", ...props } );

		this.onGlobalEvent( ( ev: EvMessage ) => {
			if( ev.msg === AUTH_LOGOUT_MSG ) {
				this.showLogin( );
			}
		} );
	}

	showLogin( ) {
		this.setContent( [ new LoginView( { flex: 1, onSuccess: ( this.props as AppShellProps ).onLoginSuccess } ) ] );
	}

	showMain( ) {
		this.setContent( [ new MainView( { flex: 1 } ) ] );
	}
}

class RosaceApp extends Application {

    constructor( ) {
		super( );

		initTooltips( );

		const shell = new AppShell( { onLoginSuccess: ( ) => {
			shell.showMain( );
			this.connectWebSocket( );
		} } );
		this.setMainView( shell );

		fetchCurrentUser( ).then( user => {
			if( user ) {
				shell.showMain( );
				this.connectWebSocket( );
			}
			else {
				shell.showLogin( );
			}
		} );

		/*
		if( __debug ) {
			app_data.observations = FAKE_OBS as any; // no warning
			app_data.status = FAKE_STATUS as any;
		}
		*/

		// update darkmode as desired
		app_data.watch( "settings.darkMode", ev => {
			document.documentElement.setAttribute('data-theme', ev.value ? 'dark' : null );
		} );

		const s = this.getStorageJSON( "settings" );
		if( s ) {
			// on valide l'entrée
			if( "darkMode" in s ) {
				app_data.settings.darkMode = !!s.darkMode;
			}
		}

		// apres load pour eviter la boucle
		// les settings ont changé -> sauvegarde
		app_data.watch( "settings", ( ev ) => {
			// on retrig si ecritures tres rapprochées
			this.setTimeout( "save", 500, ( ) => {
				this.setStorageJSON( "settings", app_data.settings );
			})
		});
    }

	/**
	 * opens the log WebSocket channel; on unexpected loss, warns visibly (not silently)
	 * and retries after WS_RETRY_DELAY — setupSocketMessaging() has no built-in reconnect
	 */

	private connectWebSocket( ) {
		this.setupSocketMessaging( undefined, ( ) => {
			new Notification( {
				title: "WebSocket déconnecté",
				text: `Reconnexion dans ${WS_RETRY_DELAY / 1000}s...`,
				mode: "danger",
				closable: true,
			} ).display( 8 );

			this.setTimeout( "ws-retry", WS_RETRY_DELAY, ( ) => this.connectWebSocket( ) );
		} );
	}
}

window.onload = ( ) => {
	new RosaceApp();
}

