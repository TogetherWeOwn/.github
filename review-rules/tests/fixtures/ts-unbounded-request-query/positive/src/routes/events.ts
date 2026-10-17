async function get() { return db.findMany({where: filter}); }
async function getAll() { return db.findMany(); }
