'use client';
import { Component, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Card } from './Card';
import { Button } from './Button';

interface State {
  hasError: boolean;
  error?: Error;
}

interface Props {
  children: ReactNode;
  /** Human-readable label used in the fallback UI ("the Returns tab"). */
  tabName?: string;
}

/**
 * Render-time error guard for a single tab / panel. Catching at the tab
 * boundary means a runtime error inside (e.g.) Recharts no longer takes
 * down the whole project page — the user can switch to another tab and
 * keep working while the failing tab shows a recoverable error UI.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // Log to the console; a future hook can ship this to Sentry/Datadog.
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info.componentStack);
  }

  reset = () => this.setState({ hasError: false, error: undefined });

  render() {
    if (this.state.hasError) {
      return (
        <Card className="p-12 text-center" role="alert" aria-live="assertive">
          <div className="w-12 h-12 rounded-lg bg-danger-50 flex items-center justify-center mx-auto mb-4">
            <AlertTriangle size={20} className="text-danger-700" aria-hidden="true" />
          </div>
          <h3 className="text-[15px] font-semibold text-ink-900">Something went wrong</h3>
          <p className="text-[12.5px] text-ink-700 mt-1 mb-4">
            {this.props.tabName ? `The ${this.props.tabName} tab` : 'This view'}{' '}
            encountered an error. The rest of the page is still usable.
          </p>
          <details className="text-[11px] text-ink-700 max-w-md mx-auto mb-4">
            <summary className="cursor-pointer">Error details</summary>
            <pre className="text-left mt-2 p-2 bg-ink-300/10 rounded text-[10px] overflow-auto">
              {this.state.error?.message}
            </pre>
          </details>
          <Button
            variant="primary"
            size="sm"
            onClick={this.reset}
            aria-label={`Retry rendering the ${this.props.tabName ?? 'tab'}`}
          >
            <RefreshCw size={12} aria-hidden="true" /> Retry
          </Button>
        </Card>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
