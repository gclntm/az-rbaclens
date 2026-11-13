import { Component, type ErrorInfo, type ReactNode } from "react";

import Button from "@/components/Button";
import { CAPTION, CARD, cx, MONO } from "@/components/classes";

type Props = { children: ReactNode; resetKey: string };
type State = { error: Error | null; at: string };

/**
 * A page that crashes while drawing shows this instead of a blank screen; the
 * side menu stays. Moving to another address (``resetKey``) tries again,
 * without remounting pages whose tabs change the address.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, at: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.at ? { error: null, at: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page crashed", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <section className={cx(CARD, "max-w-[640px]")} role="alert">
        <h1 className="mx-0 mt-0 mb-2 text-[1.375rem] font-bold">This page could not be shown</h1>
        <p className="mx-0 mt-0 mb-2">Something went wrong while drawing it. Reloading usually helps; the rest of the app still works.</p>
        <p className={cx(CAPTION, "text-quaternary", MONO, "mx-0 mt-0 mb-2 [overflow-wrap:anywhere]")}>{error.message || error.name}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" size="sm" type="button" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button size="sm" to="/">
            Go to Home
          </Button>
        </div>
      </section>
    );
  }
}
