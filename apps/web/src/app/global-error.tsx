'use client';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <div
          role="alert"
          aria-live="assertive"
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '24px',
            background: '#fff',
            color: '#1a1d24',
          }}
        >
          <div style={{ maxWidth: 480, textAlign: 'center' }}>
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 8,
                background: '#fef2f2',
                color: '#b91c1c',
                fontSize: 24,
                lineHeight: '48px',
                margin: '0 auto 16px',
                fontWeight: 600,
              }}
            >
              !
            </div>
            <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>
              Something went wrong loading Fondok
            </h1>
            <p style={{ fontSize: 13, color: '#4b5563', marginBottom: 16 }}>
              The page hit an unrecoverable error. Try again, or refresh the
              browser. If this keeps happening, share the digest below with
              the Fondok team.
            </p>
            {error.digest && (
              <p
                style={{
                  fontSize: 11,
                  fontFamily: 'monospace',
                  color: '#6b7280',
                  marginBottom: 16,
                }}
              >
                digest: {error.digest}
              </p>
            )}
            <button
              type="button"
              onClick={reset}
              style={{
                padding: '8px 16px',
                fontSize: 13,
                fontWeight: 500,
                background: '#2563eb',
                color: '#fff',
                border: 'none',
                borderRadius: 6,
                cursor: 'pointer',
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
