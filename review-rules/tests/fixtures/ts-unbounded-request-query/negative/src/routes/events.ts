async function get() { return db.findMany({where: filter, take: 20}); }
