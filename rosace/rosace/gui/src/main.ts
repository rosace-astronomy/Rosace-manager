import { VBox, HBox, Button, Label, Application, BoxProps, Image, Listbox, ListItem,
	SimpleText, SimpleTextArea, Notification, Tag, initTooltips, makeState, asap, EvMessage, HSizer,
	} from "x4js";

import "./main.scss";

import { LoginView, fetchCurrentUser, logout as authLogout, UserOut } from "./auth";

import play_icon from "./assets/play.svg";
import stop_icon from "./assets/stop.svg";
import clock_icon from "./assets/clock.svg";
import seq_icon from "./assets/sequence.svg";
import red_icon from "./assets/flask.svg";
import check_icon from "./assets/circle-check.svg";
import error_icon from "./assets/circle-exclamation.svg";

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
    priority: number | null;
    created_at: string | Date;
    error_message: string;
    stages: Record<string, StageInfo>;
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
	settings: {	//saved locally
		darkMode?: boolean;
	}
};

const app_data = makeState<AppData>( {
	status: { stage: "idle", is_running: false, session_active: false, completed_count: 0, pending_post_obs_count: 0 },
	connected: false,
	observations: [],
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

const LOG_CREATE_MSG = "log.create";
const AUTH_LOGOUT_MSG = "auth.logout";	// fired locally (Application.fireGlobal) on logout — AppShell listens

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
				new Button( { icon: "", menu: [
					{ text: "Dark mode", checked: ( ) => app_data.settings.darkMode, click: ( ) => { app_data.settings.darkMode = !app_data.settings.darkMode; } },
					{ text: "Déconnexion", click: ( ) => { authLogout().then( ( ) => Application.fireGlobal( AUTH_LOGOUT_MSG ) ); } },
				]} ),
        ] );

		app_data.watch( "status.completed_count", e => {
			label.setText( `${e.value} obs` );	// update label
		})
	}
}

/**
 * MainView is the main dashboard view
 */

class MainView extends VBox {

	private obs_list: Listbox;
	private log_lines: string[];
	private last_stage: OrchestratorStageType;
	
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
					new VBox({ cls: "panel spectre image-placeholder", flex: 1, content: [
						new Label({ cls: "title", text: "Spectre" })
					] }),
					new VBox({ cls: "panel guidage image-placeholder", flex: 1, content: [
						new Label({ cls: "title", text: "Guidage" })
					] })
				] })
			]})
		]);

		this.log( "application started" )

		// backend log records, pushed over the WebSocket log channel in periodic batches
		this.onGlobalEvent( ( ev: EvMessage ) => {
			if( ev.msg === LOG_CREATE_MSG ) {
				const lines = ev.params.lines as { level: string, text: string }[];
				this.logMany( lines.map( l => `[${l.level}] ${l.text}` ) );
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
		});
    }


	/**
	 * render observations,
	 * trigged when application.data.observations change
	 */

	private renderObs(rows: ObsRow[]) {

        this.obs_list.setItems( rows.map( obs => {
			const time = new Date(obs.created_at).toLocaleTimeString("fr-FR");

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
					const doneAt  = info.done_at ? " @ " + new Date(info.done_at).toLocaleTimeString("fr-FR") : "";

					return new Tag({
						label:    STAGE_LABEL[key].text,
						icon:     info.done_at ? check_icon : clock_icon,
						cls:      `stage ${info.status}`,
						tooltip:  `${STAGE_LABEL[key].text}: ${info.status}${doneAt}`,
					});
				}
			});

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
					new SimpleText({ cls: "obs-priority",  text: priorityText }),
					new HBox({ cls: "obs-name", content: [
						new SimpleText({ cls: "obs-target",    text: displayName }),
						new SimpleText({ cls: "obs-canonical", text: canonicalText }),
					] }),
					new SimpleText({ cls: "obs-id",        text: obs.id }),
					new SimpleText({ cls: "obs-time",      text: time }),
					new HBox({ cls: "obs-stages",  content: stages }),
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
        const ts = new Date().toLocaleTimeString("fr-FR");

        for( const msg of msgs ) {
            this.log_lines.push(`[${ts}] ${msg}`);
        }

        if (this.log_lines.length > 100) {
			this.log_lines.splice( 0, this.log_lines.length - 100 );
		}

		const l = this.query<SimpleTextArea>( "#logger" );
        l.setText( this.log_lines.join('\n' ) );
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

