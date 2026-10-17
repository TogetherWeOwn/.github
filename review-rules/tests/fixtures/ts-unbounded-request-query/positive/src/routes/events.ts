async function get() { return db.findMany({where: filter}); }
