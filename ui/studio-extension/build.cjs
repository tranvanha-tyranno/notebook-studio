const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const build = spawnSync(process.execPath, [
  require.resolve('@jupyterlab/builder/lib/build-labextension.js'),
  '--core-path', 'core', '.'
], {cwd: __dirname, stdio: 'inherit'});
if (build.status !== 0) process.exit(build.status || 1);
// Builds on Windows are served by Linux; the federated module URL needs slashes.
const target = path.join(__dirname, '../jupyter/labextension/package.json');
const pkg = JSON.parse(fs.readFileSync(target, 'utf8'));
pkg.jupyterlab._build.load = pkg.jupyterlab._build.load.replaceAll('\\', '/');
fs.writeFileSync(target, JSON.stringify(pkg, null, 2) + '\n');
