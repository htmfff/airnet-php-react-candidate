import { build } from 'esbuild';

await build({
  entryPoints: ['test/race.entry.jsx'],
  bundle: true,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"development"' },
  outfile: 'test/.build/race.bundle.mjs',
  logLevel: 'warning',
});
