// Browser-independent regression for unusually dense CAD preview arrays.
// No third-party packages; verifies the real draw() function in index.html.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync("cad_audit/static/index.html", "utf8");
const start = html.indexOf("function draw(root,objects,active){");
const end = html.indexOf("\n}\n</script>", start);
assert.ok(start >= 0 && end > start, "draw function is present");
const code = html.slice(start, end + 2);
class Element {
  constructor(type) { this.type = type; this.children = []; this.attributes = {}; }
  replaceChildren(...items) { this.children = items; }
  append(node) { this.children.push(node); }
  setAttribute(key, value) { this.attributes[key] = value; }
}
const doc = { createElementNS: (_, type) => new Element(type) };
const el = (kind, text) => {
  const node = new Element(kind); node.textContent = text || ""; return node;
};
const draw = vm.runInNewContext("(" + code + ")", { document: doc, el });
const data = Array.from({ length: 150000 }, (_, i) => [i, i % 17]);
const root = new Element("container");
draw(root, [{ type: "POLYLINE", id: "L1", pts: data, closed: false }], null);
assert.equal(root.children.length, 1);
assert.equal(root.children[0].type, "svg");
assert.ok(root.children[0].children.length >= 1);
assert.equal(root.children[0].children[0].type, "polyline");
console.log("Large CAD preview function passed: 150000 vertices");
