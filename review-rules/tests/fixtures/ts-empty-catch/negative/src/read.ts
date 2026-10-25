async function f() { try { return await read(); } catch (e) { log(e.code); throw e; } }
