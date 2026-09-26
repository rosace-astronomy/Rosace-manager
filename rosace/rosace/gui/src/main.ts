import { VBox, HBox, Button, Label, Application, BoxProps, Image, Listbox, ListItem,
	SimpleText, SimpleTextArea, Notification, Tag, initTooltips, makeState, asap, EvMessage, HSizer,
	Component,
	} from "x4js";

import "./main.scss";

import { LoginView, fetchCurrentUser, logout as authLogout, UserOut } from "./auth";
import { FitsViewer, SlitOverlay } from "./widgets/fits_viewer";

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
}

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
	status: { stage: "idle", is_running: false, session_active: false, completed_count: 0, pending_post_obs_count: 0 },
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
	stage: "idle"
}



// ::: Constants ::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::

const API = "";

const OBS_FETCH_TIME = 3000;
const STATUS_FETCH_TIME = 5000;
const WS_RETRY_DELAY = 5000;

// How long the SEQUENCER/REDUCTION step-progress lines stay visible, showing
// their last state ("all done"), after the whole observation finishes
// (ARCHIVE's own done_at) -- purely a display grace period, not tied to
// anything server-side; step_progress.py itself never expires an entry.
const STEP_PROGRESS_HOLD_SECONDS = 10;

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

// focus sub-plugin's own search-window fraction (GET /config/focus), fetched once at
// startup -- same "static for the server's lifetime" reasoning as subplugin_display_names
// above. null when unconfigured (or not yet fetched): the Guidage panel then never draws
// the reference rectangle at all -- see loadPanelImage/fetchFocusConfig below.
let focus_window_fraction: number | null = null;

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

class AppHeader extends HBox {
	
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
					new Label( { id: "title", text: "Rosace — Robotic Spectro" })
				] }),
                new HBox({ cls: "pipeline-bar", content: chips }),
                label = new Label({ cls: "obs-count-badge", text: "0 obs" }),
				new Button( { id: "darkmode", icon: moon_icon, tooltip: "Réglages", menu: [
					{ text: "Dark mode", checked: ( ) => app_data.settings.darkMode, click: ( ) => { app_data.settings.darkMode = !app_data.settings.darkMode; } },
				]} ),
				new Button( { id: "logout", icon: logout_icon, tooltip: "Déconnexion", click: ( ) => { authLogout().then( ( ) => Application.fireGlobal( AUTH_LOGOUT_MSG ) ); } } ),
        ] );

		app_data.watch( "status.completed_count", e => {
			label.setText( `${e.value} obs` );	// update label
		})
	}
}

/**
 * Application Footer — UTC clock on the left, browser-local clock on the right,
 * both ticking every second. Both sides deliberately share the exact same
 * formatting rule (24h, no AM/PM) as the rest of the app's time displays
 * (see logMany()/renderObs() above) — the whole point of showing both side by
 * side is to let the observer compare them at a glance, which only works if
 * they're in the same format.
 */

class AppFooter extends HBox {

	constructor( props: BoxProps ) {
		super( { cls: "app-footer", ...props } );

		const formatUtc   = ( now: Date ) => "UTC   " + now.toLocaleTimeString( undefined, { timeZone: "UTC", hour12: false } );
		const formatLocal = ( now: Date ) => "Local " + now.toLocaleTimeString( undefined, { hour12: false } );

		const utcLabel   = new Label( { cls: "clock utc-clock",   text: formatUtc( new Date( ) ) } );
		const localLabel = new Label( { cls: "clock local-clock", text: formatLocal( new Date( ) ) } );
		this.setContent( [ utcLabel, localLabel ] );

		// named/repeating timer (x4js CoreElement) — same idiom as fetchStatus()/
		// fetchObs()'s own polling below; holds only a WeakRef on this component,
		// so it self-clears once this AppFooter is no longer reachable, with no
		// dispose hook needed.
		this.setInterval( "clock", 1000, ( ) => {
			const now = new Date( );
			utcLabel.setText( formatUtc( now ) );
			localLabel.setText( formatLocal( now ) );
		} );
	}
}

/**
 * MainView is the main dashboard view
 */

class MainView extends VBox {

	private obs_list: Listbox;
	private log_lines: string[];
	private last_stage: OrchestratorStageType;
	private spectre_viewer: FitsViewer;
	private guidage_viewer: FitsViewer;
	// Tracks, per panel, whether its first real (non-placeholder) image has
	// already received its automatic stretch -- see loadPanelImage below.
	private autoStretched: Record<string, boolean> = {};

	constructor( props: BoxProps ) {
		super( props );

		this.log_lines = [];

		this.setContent( [
			new AppHeader( { } ),

			new HBox( { cls: "main-split", flex: 1, content: [
				new VBox( { cls: "left-col", flex: 1, content: [
					//<todo: x4btngroup
					new HBox( { cls: "controls-bar", content: [
						//<todo: use button.icon
						new Button({ label: "START", icon: play_icon, id: "start", click: () => this.controlStart() }),
						//<todo: use button.icon
						new Button({ label: "STOP", icon: stop_icon, id: "stop",  click: () => this.controlStop() }),
						//new SimpleText({ flex: 1, id: "status-text", text: "text" }), // replaced by notification
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
				new VBox({ cls: "right-col", flex: 1, content: [
					new VBox({ cls: "panel spectre", flex: 1, content: [
						new Label({ cls: "title", text: "Spectre" }),
						this.spectre_viewer = new FitsViewer({ flex: 1 }),
					] }),
					new VBox({ cls: "panel guidage", flex: 1, content: [
						new Label({ cls: "title", text: "Guidage" }),
						this.guidage_viewer = new FitsViewer({ flex: 1 }),
					] })
				] })
			]}),

			new AppFooter( { } ),
		]);

		this.log( "application started" )

		// backend log records, pushed over the WebSocket log channel in periodic batches
		this.onGlobalEvent( ( ev: EvMessage ) => {
			if( ev.msg === LOG_CREATE_MSG ) {
				const lines = ev.params.lines as { level: string, text: string }[];
				this.logMany( lines.map( l => `[${l.level}] ${l.text}` ) );
			}
			else if( ev.msg === IMAGE_NEW_MSG ) {
				const { panel, source, overlay } = ev.params as { panel: string, source?: string, overlay?: SlitOverlay };
				this.loadPanelImage( panel );
				if( panel === "guidage" ) {
					// Only a focus.py capture gets the reference window rectangle, and only a
					// slit_xy.py capture the slit overlay -- each explicitly cleared (not just
					// left alone) on any other source, so it doesn't linger over the next
					// image pushed to this same panel.
					this.guidage_viewer.setReferenceWindowFraction( source === "focus" ? focus_window_fraction : null );
					this.guidage_viewer.setSlitOverlay( source === "slit_xy" && overlay ? overlay : null );
				}
			}
		} );

		// watch observations
		app_data.watch( "observations", e => {
			this.renderObs( e.value );
		})

		// watch status
		app_data.watch( "status", e => {
			this.updateStageChips( e.value );
		} );
			
		// attendons d'etre montés dans l'UI
		asap( () => {
			this.fetchStatus();
			this.fetchObs();
			this.loadDemoImages();
			this.fetchDisplayNames();
			this.fetchFocusConfig();
		});
    }

	/**
	 * Temporary: loads a bundled demo FITS into both viewers as a startup placeholder,
	 * replaced by a real image as soon as one is pushed (see loadPanelImage below) --
	 * "Spectre" isn't wired to a real push yet, so it keeps showing the demo image.
	 */

	private async loadDemoImages() {
		const buffer = await ( await fetch( "assets/demo.fits" ) ).arrayBuffer();
		this.spectre_viewer.loadFits( buffer );
		this.guidage_viewer.loadFits( buffer );
	}

	/**
	 * Fetched once, at startup -- see subplugin_display_names' own comment above.
	 */

	/**
	 * Fetched once, at startup -- see focus_window_fraction's own comment above.
	 */

	private async fetchFocusConfig() {
		try {
			const res = await fetch( `${API}/config/focus`, { signal: AbortSignal.timeout(5000) } );
			if( res.ok ) {
				const body = await res.json() as { window_fraction: number | null };
				focus_window_fraction = body.window_fraction;
			}
		}
		catch( e ) {
			this.showError( `focus config fetch error`, e.toString() );
		}
	}

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
	 * image_broadcast.py) and displays it. panel is "guidage" or "spectre".
	 */

	private async loadPanelImage( panel: string ) {
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

		viewer.loadFits( await res.arrayBuffer() );

		// first real image on this panel: apply the automatic (whole-image)
		// percentile stretch once, so it doesn't come up all-black by default --
		// never touches the startup placeholder (loadDemoImages), and never
		// re-applies on later images (a manual "Ajuster les niveaux" click
		// takes over from here, same persistence rule as any other adjustment).
		if( !this.autoStretched[panel] ) {
			viewer.adjustLevels();
			this.autoStretched[panel] = true;
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
			this.query("#start").enable(!data.session_active);
			this.query("#stop").enable(data.is_running);

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
            bt.enable( );
        }
    }

    private async controlStop() {
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
            bt.enable( );
        }
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

