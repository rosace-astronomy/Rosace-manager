import { VBox, HBox, Button, Label, Component, Box, Icon } from "./x4js/x4";

// ─── Types ────────────────────────────────────────────────────────────────────

interface StageInfo {
    status: "pending" | "running" | "done" | "error";
    done_at: string | null;
}

interface ObsRow {
    id: string;
    target_name: string;
    created_at: string;
    error_message: string | null;
    stages: Record<string, StageInfo>;
}

interface ApiStatus {
    stage: string;
    is_running: boolean;
    completed_count: number;
    pending_post_obs_count: number;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const API = "/api";

const STAGES = ["scheduler", "sequencer", "reduction", "sharing", "archive"] as const;

const STAGE_LABEL: Record<string, string> = {
    scheduler: "SCHED",
    sequencer: "SEQ",
    reduction: "RED",
    sharing:   "SHARE",
    archive:   "ARC",
};

const PIPELINE_STAGES = [
    { key: "idle",          label: "IDLE"      },
    { key: "init_session",  label: "INIT"      },
    { key: "observing",     label: "OBSERVING" },
    { key: "close_session", label: "CLOSE"     },
] as const;

const OBS_SUB_STAGES = new Set(["scheduler", "sequencer", "reduction", "sharing", "archive"]);

// ─── App ──────────────────────────────────────────────────────────────────────

class RosaceApp {
    private stageChips: Label[] = [];
    private obsCountBadge!: Label;
    private startBtn!: Button;
    private stopBtn!: Button;
    private msgLabel!: Label;
    private obsListBox!: VBox;
    private logBox!: VBox;

    private lastStage: string | null = null;
    private logLines: string[] = [];
    private apiConnected = false;

    constructor() {
        const root = this.buildRoot();
        document.body.appendChild(root.dom);
        this.startPolling();
    }

    // ── Layout ────────────────────────────────────────────────────────────────

    private buildRoot(): VBox {
        return new VBox({
            cls: "app-root",
            content: [
                this.buildHeader(),
                this.buildMain(),
            ],
        });
    }

    private buildHeader(): HBox {
        const logoImg = new Component({
            tag: "img",
            cls: "logo-img",
            attrs: { src: "assets/logo.svg", alt: "Logo" },
        });
        const logoTitle = new Label({ cls: "logo-title", text: "Rosace — Robotic Spectro" });

        const chipsWithSeps: Component[] = [];
        PIPELINE_STAGES.forEach((s, i) => {
            if (i > 0) chipsWithSeps.push(new Label({ cls: "chip-sep", text: "›" }));
            const chip = new Label({ cls: "chip chip-dim", text: s.label });
            this.stageChips.push(chip);
            chipsWithSeps.push(chip);
        });
        const pipelineBar = new HBox({ cls: "pipeline-bar", content: chipsWithSeps });

        this.obsCountBadge = new Label({ cls: "obs-count-badge", text: "0 obs" });

        return new HBox({
            cls: "app-header",
            content: [
                new HBox({ cls: "header-logo", content: [logoImg, logoTitle] }),
                pipelineBar,
                this.obsCountBadge,
            ],
        });
    }

    private updateStageChips(stage: string, isRunning: boolean): void {
        let activeKey: string;
        if (!isRunning)                      activeKey = "idle";
        else if (stage === "init_session")   activeKey = "init_session";
        else if (OBS_SUB_STAGES.has(stage))  activeKey = "observing";
        else if (stage === "close_session")  activeKey = "close_session";
        else                                 activeKey = "idle";

        PIPELINE_STAGES.forEach((s, i) => {
            const chip = this.stageChips[i];
            chip.removeClass("chip-dim chip-active chip-error");
            if (s.key === activeKey) {
                chip.addClass(stage === "error" ? "chip-error" : "chip-active");
            } else {
                chip.addClass("chip-dim");
            }
        });
    }

    private buildControls(): HBox {
        this.startBtn = new Button({ label: "▶  START", cls: "btn-ctrl btn-start", click: () => this.controlStart() });
        this.stopBtn  = new Button({ label: "■  STOP",  cls: "btn-ctrl btn-stop",  click: () => this.controlStop() });
        this.msgLabel = new Label({ cls: "ctrl-msg", text: "" });

        return new HBox({
            cls: "controls-bar",
            content: [this.startBtn, this.stopBtn, this.msgLabel],
        });
    }

    private buildMain(): HBox {
        return new HBox({
            cls: "main-area",
            content: [this.buildLeftCol(), this.buildRightCol()],
        });
    }

    private buildObsSection(): VBox {
        this.obsListBox = new VBox({ cls: "obs-list" });
        return new VBox({
            cls: "panel panel-obs",
            content: [
                new Label({ cls: "panel-title", text: "Observations" }),
                this.obsListBox,
            ],
        });
    }

    private buildLeftCol(): VBox {
        this.logBox = new VBox({ cls: "log-box" });
        const logPanel = new VBox({
            cls: "panel panel-log",
            content: [
                new Label({ cls: "panel-title", text: "Journal" }),
                this.logBox,
            ],
        });
        return new VBox({
            cls: "left-col",
            content: [this.buildControls(), this.buildObsSection(), logPanel],
        });
    }

    private buildRightCol(): VBox {
        const spectrum = new VBox({
            cls: "image-placeholder",
            content: [new Label({ cls: "placeholder-label", text: "Spectre" })],
        });
        const guiding = new VBox({
            cls: "image-placeholder",
            content: [new Label({ cls: "placeholder-label", text: "Guidage" })],
        });

        return new VBox({ cls: "right-col", content: [spectrum, guiding] });
    }

    // ── Polling ───────────────────────────────────────────────────────────────

    private startPolling(): void {
        this.fetchStatus();
        this.fetchObs();
        setInterval(() => this.fetchStatus(), 5000);
        setInterval(() => this.fetchObs(),    5000);
    }

    private async fetchStatus(): Promise<void> {
        try {
            const res = await fetch(`${API}/status`, { signal: AbortSignal.timeout(5000) });
            if (!res.ok) return;
            const data = await res.json() as ApiStatus;
            if (!this.apiConnected) {
                this.apiConnected = true;
                this.log(`API connected — ${data.is_running ? "session running" : "idle"}`);
            }
            this.applyStatus(data);
        } catch {
            if (this.apiConnected) {
                this.apiConnected = false;
                this.log("API disconnected");
            }
        }
    }

    private applyStatus(d: ApiStatus): void {
        this.updateStageChips(d.stage, d.is_running);
        this.obsCountBadge.setText(`${d.completed_count} obs`);
        this.startBtn.enable(!d.is_running);
        this.stopBtn.enable(d.is_running);

        if (d.stage !== this.lastStage) {
            if (this.lastStage !== null) {
                const stageLabel = PIPELINE_STAGES.find(s => s.key === d.stage)?.label ?? d.stage.toUpperCase();
                this.log(`Stage → ${stageLabel}`);
            }
            this.lastStage = d.stage;
        }
    }

    private async fetchObs(): Promise<void> {
        try {
            const res = await fetch(`${API}/observations/recent?limit=20`, { signal: AbortSignal.timeout(5000) });
            if (!res.ok) return;
            this.renderObs(await res.json() as ObsRow[]);
        } catch { /* API unreachable */ }
    }

    private renderObs(rows: ObsRow[]): void {
        if (!rows.length) {
            this.obsListBox.setContent([new Label({ cls: "obs-empty", text: "Aucune observation." })]);
            return;
        }
        this.obsListBox.setContent(rows.map(r => this.buildObsRow(r)));
    }

    private buildObsRow(obs: ObsRow): HBox {
        const time = new Date(obs.created_at).toLocaleTimeString("fr-FR");

        const stageBtns = STAGES.map(key => {
            const info    = obs.stages[key];
            const doneAt  = info.done_at ? " @ " + new Date(info.done_at).toLocaleTimeString("fr-FR") : "";
            return new Button({
                label:    STAGE_LABEL[key],
                cls:      `stage-btn stage-${info.status}`,
                tabindex: false,
                tooltip:  `${STAGE_LABEL[key]}: ${info.status}${doneAt}`,
            });
        });

        return new HBox({
            cls: `obs-row${obs.error_message ? " obs-has-error" : ""}`,
            content: [
                new Label({ cls: "obs-id",     text: obs.id }),
                new Label({ cls: "obs-target", text: obs.target_name }),
                new Label({ cls: "obs-time",   text: time }),
                new HBox({ cls: "obs-stages",  content: stageBtns }),
            ],
        });
    }

    // ── Controls ──────────────────────────────────────────────────────────────

    private async controlStart(): Promise<void> {
        this.startBtn.enable(false);
        try {
            const res  = await fetch(`${API}/control/start`, { method: "POST", signal: AbortSignal.timeout(5000) });
            const data = await res.json() as { message: string };
            this.log(`START → ${data.message}`);
            this.showMsg(data.message);
            await this.fetchStatus();
        } catch (e) {
            this.log(`Erreur START : ${e}`);
            this.startBtn.enable(true);
        }
    }

    private async controlStop(): Promise<void> {
        this.stopBtn.enable(false);
        try {
            const res  = await fetch(`${API}/control/stop`, { method: "POST", signal: AbortSignal.timeout(5000) });
            const data = await res.json() as { message: string };
            this.log(`STOP → ${data.message}`);
            this.showMsg(data.message);
            await this.fetchStatus();
        } catch (e) {
            this.log(`Erreur STOP : ${e}`);
            this.stopBtn.enable(true);
        }
    }

    // ── Log ───────────────────────────────────────────────────────────────────

    private log(msg: string): void {
        const ts = new Date().toLocaleTimeString("fr-FR");
        this.logLines.push(`${ts}  ${msg}`);
        if (this.logLines.length > 100) this.logLines.shift();
        this.logBox.setContent(this.logLines.map(l => new Label({ cls: "log-line", text: l })));
        const el = this.logBox.dom as HTMLElement;
        el.scrollTop = el.scrollHeight;
    }

    private showMsg(msg: string): void {
        this.msgLabel.setText(msg);
        setTimeout(() => this.msgLabel.setText(""), 5000);
    }
}

new RosaceApp();
