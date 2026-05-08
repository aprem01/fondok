/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  // Permanent redirect any traffic that lands on the legacy
  // fondok-one.vercel.app hostname back to the canonical
  // fondok-app.vercel.app deployment. The hostname-conditional `has`
  // ensures fondok-app traffic isn't touched. Lives in next.config.js
  // (not vercel.json) because the Next.js framework owns routing —
  // vercel.json redirects are bypassed for Next.js builds.
  async redirects() {
    return [
      {
        source: '/:path*',
        has: [{ type: 'host', value: 'fondok-one.vercel.app' }],
        destination: 'https://fondok-app.vercel.app/:path*',
        permanent: true,
      },
    ];
  },
};
