async function restore(path) { await fs.rm(path, {recursive: true}); }
