import { VBox, Label, Button, Form, Input, BoxProps, Notification } from "x4js";

const API = "";

export interface UserOut {
	username: string;
	is_admin: boolean;
	created_at: string;
}

export async function login( username: string, password: string ): Promise<UserOut> {
	const res = await fetch( `${API}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify( { username, password } ),
		signal: AbortSignal.timeout( 5000 ),
	} );

	if( !res.ok ) {
		throw new Error( res.status===401 ? "Identifiant ou mot de passe incorrect" : res.statusText );
	}

	return await res.json() as UserOut;
}

export async function logout(): Promise<void> {
	await fetch( `${API}/auth/logout`, { method: "POST", signal: AbortSignal.timeout( 5000 ) } );
}

/**
 * Current user, or null if not logged in (or the session expired) — never throws,
 * so callers can use it as a plain "am I logged in?" check on boot.
 */

export async function fetchCurrentUser(): Promise<UserOut | null> {
	try {
		const res = await fetch( `${API}/auth/me`, { signal: AbortSignal.timeout( 5000 ) } );
		if( !res.ok ) {
			return null;
		}
		return await res.json() as UserOut;
	}
	catch {
		return null;
	}
}

interface LoginViewProps extends BoxProps {
	onSuccess: ( user: UserOut ) => void;
}

/**
 * Full-page login form — shown by AppShell (main.ts) before MainView, until
 * fetchCurrentUser() (or a successful login here) resolves to a real user.
 */

export class LoginView extends VBox {

	private form: Form;

	constructor( props: LoginViewProps ) {
		super( { cls: "login-view", ...props } );

		this.setContent( [
			new VBox( { cls: "login-panel", content: [
				new Label( { cls: "login-title", text: "Connexion", align: "center" } ),
				this.form = new Form( { cls: "login-form", content: [
					new VBox( { cls: "field", content: [
						new Label( { text: "Identifiant" } ),
						new Input( { name: "username", type: "text", required: true, autofocus: true } ),
					] } ),
					new VBox( { cls: "field", content: [
						new Label( { text: "Mot de passe" } ),
						new Input( { name: "password", type: "password", required: true } ),
					] } ),
					new Button( { label: "Se connecter", id: "login-submit", click: ( ) => this.submit() } ),
				] } ),
			] } ),
		] );

		// x4js's Button._on_click() always calls ev.preventDefault() on click — with or
		// without a "click" prop — which blocks the native submit-on-click behaviour a
		// <button> would otherwise trigger inside a <form>. So clicking never reaches this
		// "submit" listener; it only exists for pressing Enter in a field, which triggers
		// the form's native submission directly rather than going through the button.
		this.form.addDOMEvent( "submit", ( e: Event ) => {
			e.preventDefault();
			this.submit();
		} );
	}

	private async submit() {
		const btn = this.query( "#login-submit" );
		const { username, password } = this.form.getValues<{ username: string, password: string }>();

		btn.disable();
		try {
			const user = await login( username, password );
			( this.props as LoginViewProps ).onSuccess( user );
		}
		catch( e ) {
			new Notification( { title: "Connexion refusée", text: e.toString(), mode: "danger" } ).display( 5 );
		}
		finally {
			btn.enable();
		}
	}
}
