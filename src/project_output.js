/* Shared project-folder saving for 12 zoning and 14 area builders. */
window.ProjectOutput = (() => {
  let project = null;
  async function choose() {
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
  return {choose, save};
})();
