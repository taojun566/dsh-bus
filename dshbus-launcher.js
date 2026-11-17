#!/usr/bin/env node
// dsh-bus MCP launcher: spawns the Python MCP server shipped in this package.
// Portable entry for `npx -y dsh-bus` and for DSH bundle installs (command: npx).
// Python resolution order: DSHBUS_PYTHON env -> python3 (non-Windows) -> python -> py -3 (Windows).
'use strict';
const { spawn } = require('child_process');
const path = require('path');

const serverPy = path.join(__dirname, 'dshbus_mcp.py');

const candidates = [];
if (process.env.DSHBUS_PYTHON) candidates.push(process.env.DSHBUS_PYTHON);
if (process.platform !== 'win32') candidates.push('python3');
candidates.push('python');
if (process.platform === 'win32') candidates.push(['py', '-3']);

function tryNext(i) {
  if (i >= candidates.length) {
    console.error('[dsh-bus] No Python >= 3.10 found on PATH.');
    console.error('[dsh-bus] Install Python 3.10+ or point DSHBUS_PYTHON at the interpreter.');
    process.exit(127);
  }
  const c = candidates[i];
  const bin = Array.isArray(c) ? c[0] : c;
  const pre = Array.isArray(c) ? c.slice(1) : [];
  const child = spawn(bin, pre.concat(['-X', 'utf8', serverPy]), {
    stdio: ['inherit', 'inherit', 'inherit'],
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
    windowsHide: true,
  });
  // Launcher itself never writes to stdout: stdin/stdout are the MCP JSON-RPC channel.
  child.on('error', (e) => {
    if (e.code === 'ENOENT') tryNext(i + 1);
    else { console.error('[dsh-bus] spawn failed:', e.message); process.exit(1); }
  });
  child.on('exit', (code, sig) => process.exit(code == null ? (sig ? 1 : 0) : code));
}
tryNext(0);
