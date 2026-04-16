/**
 * A render-time error boundary.
 *
 * In React, an exception thrown while rendering unmounts the *entire* tree
 * unless something catches it. The user gets a white page with no explanation
 * and no way back — which is what happened the first time an event pin was
 * clicked and the inspector referenced a component that did not exist. The
 * analysis was fine, the audio was fine, and the tool looked broken.
 *
 * Two boundaries, because the cost of losing each is different:
 *   - `PanelBoundary` wraps each inspector panel. One bad panel loses one
 *     panel, and the timeline and transport keep working.
 *   - `AppBoundary` wraps the app. It exists to show the stack rather than a
 *     blank page, and to give a reset button.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Shown above the error, e.g. "Event inspector". */
  label?: string
  /** Keep the error visible but let the panel be retried. */
  compact?: boolean
  onReset?: () => void
}

interface State {
  error: Error | null
  info: string
}

class Boundary extends Component<Props, State> {
  state: State = { error: null, info: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the first few frames only; a full React stack is mostly noise.
    const stack = (error.stack ?? '').split('\n').slice(0, 6).join('\n')
    this.setState({ info: `${info.componentStack ?? ''}${stack}`.trim() })
    // eslint-disable-next-line no-console
    console.error('[boundary]', this.props.label ?? 'app', error)
  }

  private reset = () => {
    this.setState({ error: null, info: '' })
    this.props.onReset?.()
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children
    if (this.props.compact) {
      return (
        <div className="boundary compact" role="alert">
          <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
            <strong className="small">
              {this.props.label ?? 'This panel'} could not be displayed
            </strong>
            <button className="ghost tiny" onClick={this.reset}>retry</button>
          </div>
          <code className="tiny faint mono" style={{ display: 'block', marginTop: 4 }}>
            {error.message}
          </code>
        </div>
      )
    }
    return (
      <div className="boundary" role="alert">
        <h2>Something broke while drawing this view</h2>
        <p className="dim small">
          Your analysis and audio are untouched — this is a display fault, and the
          rest of the tool will keep working once you dismiss it.
        </p>
        <pre className="mono tiny">{error.message}{'\n\n'}{info}</pre>
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <button className="primary" onClick={this.reset}>Try again</button>
          <button onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    )
  }
}

export function PanelBoundary({ children, label }: { children: ReactNode; label: string }) {
  // Keyed by label so switching songs resets a boundary that caught an error
  // for a song that no longer exists.
  return <Boundary compact label={label} key={label}>{children}</Boundary>
}

export function AppBoundary({ children }: { children: ReactNode }) {
  return <Boundary>{children}</Boundary>
}
