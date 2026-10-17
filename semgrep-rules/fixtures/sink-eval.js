// Fixture: every sink in this file must fire tier0-no-eval.
function risky(userInput, body) {
  const direct = eval(userInput);
  const constructed = new Function(body);
  const benign = eval("constant");
  return [direct, constructed, benign];
}
