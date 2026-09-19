/* Shared project-folder saving for 12 zoning and 14 area builders. */
window.ProjectOutput = (() => {
  let project = null;
  const native = typeof qt !== 'undefined' ? new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'qrc:///qtwebchannel/qwebchannel.js';
    script.onload = () => new QWebChannel(qt.webChannelTransport, channel => resolve(channel.objects.projectBridge));
    script.onerror = () => reject(new Error('保存機能を読み込めませんでした。'));
    document.head.appendChild(script);
  }) : null;
  async function call(method, ...args) {
    const bridge = await native;
    return new Promise((resolve, reject) => bridge[method](...args, text => {
      const result = JSON.parse(text);
      if (!result.ok) return reject(new Error(result.error));
      if (result.cancelled) { const error = new Error('キャンセル'); error.name = 'AbortError'; return reject(error); }
      resolve(result);
    }));
  }
  if (native) {
    call('getProject').then(result => {
      project = result.project;
      const status = document.getElementById('projectStatus');
      if (status && project) status.textContent = project + '/12_ゾーニングデータ';
    }).catch(error => alert(error.message));
  }
  async function choose() {
    if (native) { project = (await call('chooseProject')).project; return project; }
    if (!window.showDirectoryPicker) {
      throw new Error('プロジェクトへの直接保存にはEdgeまたはChromeを使用してください。');
    }
    project = await window.showDirectoryPicker({mode: 'readwrite'});
    return project.name;
  }
  async function save(folder, filename, blob) {
    if (!filename || /[\\/:*?"<>|]/.test(filename) || /[. ]$/.test(filename)) {
      throw new Error('ファイル名に使用できない文字があります。');
    }
    if (!project) await choose();
    if (native) {
      const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = '';
      for (const byte of bytes) binary += String.fromCharCode(byte);
      return (await call('saveFile', folder, filename, btoa(binary))).path;
    }
    const directory = await project.getDirectoryHandle(folder, {create: true});
    const file = await directory.getFileHandle(filename, {create: true});
    const writable = await file.createWritable();
    try {
      await writable.write(blob);
      await writable.close();
    } catch (error) {
      await writable.abort().catch(() => {});
      throw error;
    }
    return `${project.name}/${folder}/${filename}`;
  }
  async function open(kind) {
    if (!native) throw new Error('ネイティブ画面から起動してプロジェクトを選択してください。');
    return call('openFile', kind);
  }
  return {choose, save, open, isNative: Boolean(native)};
})();
