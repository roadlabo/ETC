const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'src/project_output.js'), 'utf8');

function setup(picker) {
  const context = {window: {showDirectoryPicker: picker}};
  vm.runInNewContext(source, context);
  return context.window.ProjectOutput;
}

(async () => {
  const saved = [];
  let picked = 0;
  const project = {name: 'Project', async getDirectoryHandle(folder, options) {
    assert.equal(options.create, true);
    return {async getFileHandle(filename) {
      return {async createWritable() {
        let content;
        return {
          async write(blob) { content = blob; },
          async close() { saved.push({folder, filename, content}); },
          async abort() { throw Error('Unexpected abort'); }
        };
      }};
    }};
  }};
  const output = setup(async () => { picked++; return project; });
  assert.equal(await output.save('12_ゾーニングデータ', '西.csv', 'csv'), 'Project/12_ゾーニングデータ/西.csv');
  assert.equal(await output.save('14_エリアデータ', '14_area.geojson', 'geojson'), 'Project/14_エリアデータ/14_area.geojson');
  assert.equal(picked, 1);
  assert.deepEqual(saved, [
    {folder: '12_ゾーニングデータ', filename: '西.csv', content: 'csv'},
    {folder: '14_エリアデータ', filename: '14_area.geojson', content: 'geojson'}
  ]);
  await assert.rejects(output.save('12_ゾーニングデータ', '../bad.csv', 'bad'), /ファイル名/);
  const cancelled = setup(async () => { throw Object.assign(Error('cancelled'), {name: 'AbortError'}); });
  await assert.rejects(cancelled.save('14_エリアデータ', '14_area.geojson', ''), {name: 'AbortError'});
  await assert.rejects(setup(undefined).choose(), /Edge/);
  let aborted = false;
  const failing = setup(async () => ({name: 'Project', async getDirectoryHandle() {
    return {async getFileHandle() { return {async createWritable() {
      return {async write() { throw Error('disk full'); }, async abort() { aborted = true; }};
    }}; }};
  }}));
  await assert.rejects(failing.save('14_エリアデータ', '14_area.geojson', ''), /disk full/);
  assert.equal(aborted, true);
  for (const name of ['12_polygon_builder.html', '14_area_builder.html']) {
    const html = fs.readFileSync(path.join(root, 'src', name), 'utf8');
    assert.ok(html.includes(name.startsWith('12') ? 'src="project_output.js"' : 'src="qrc:///qtwebchannel/qwebchannel.js"'));
    for (const [, script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(script, {filename: name});
  }
  const polygonHtml = fs.readFileSync(path.join(root, 'src/12_polygon_builder.html'), 'utf8');
  const parser = polygonHtml.slice(polygonHtml.indexOf('  function parseCsvText'), polygonHtml.indexOf('  function csvRowsToPolygons'));
  const context = {};
  vm.runInNewContext(parser, context);
  const parsed = context.parseCsvText('\uFEFF"西,東""町",134,35\r\n');
  assert.equal(parsed[0][0], '西,東"町');
  assert.equal(parsed[0][1], '134');
  console.log('Project saving, cancellation, failures, and builder JavaScript: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
