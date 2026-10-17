// Fixture: the interpolated exec below must fire tier0-no-shell-exec-interpolation.
const child_process = require("node:child_process");

function listDir(dir) {
  return child_process.exec(`ls ${dir}`);
}
