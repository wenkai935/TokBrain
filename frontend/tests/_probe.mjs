import { readFile } from "node:fs/promises";

const read = (p) => readFile(new URL(p, import.meta.url), "utf8");
const pageShell = await read("../app/page.tsx");
const libraryComponent = await read("../components/Library.tsx");
const settingsComponent = await read("../components/Settings.tsx");
const importComponent = await read("../components/ImportWorkspace.tsx");
const parseComponent = await read("../components/ParseWorkspace.tsx");
const cookieComponent = await read("../components/CookieVault.tsx");

const OLD = [pageShell, libraryComponent, settingsComponent].join("\n");
const NEW = [pageShell, libraryComponent, settingsComponent, importComponent, parseComponent, cookieComponent].join("\n");

const assertions = [
  ["7  risk role=alert", /className="risk-notice" role="alert"/, false],
  ["7  下载权限文案", /仅当平台明确返回作者允许下载时才处理完整视频/, false],
  ["8  import-textarea", /className="import-textarea"/, false],
  ["8  每行粘贴抖音链接", /每行粘贴一个抖音作品链接/, false],
  ["8  batch-progress", /className="batch-progress"/, false],
  ["8  worker-grid", /className="worker-grid"/, false],
  ["8  中断解析", /中断解析/, false],
  ["8  confirmedWorkIds", /confirmedWorkIds/, false],
  ["8  api.ingest(workIds)", /api\.ingest\(workIds\)/, false],
  ["8  已勾选待确认", /已勾选待确认/, false],
  ["8  samePreviewWork", /samePreviewWork/, false],
  ["8  selectedVisibleIds", /selectedVisibleIds/, false],
  ["9  添加抖音作品链接", /添加抖音作品链接/, false],
  ["9  预检后由您确认", /预检后由您确认/, false],
  ["9  开始预检", /开始预检/, false],
  ["9  createImportBatch(text)", /createImportBatch\(\{ text: submittedText \}\)/, false],
  ["10 导入外部视频数据包", /导入外部视频数据包/, false],
  ["10 选择文件夹", /选择文件夹/, false],
  ["10 选择 ZIP", /选择 ZIP/, false],
  ["10 douyin_videos.db", /douyin_videos\.db/, false],
  ["10 createPackageImportBatch", /api\.createPackageImportBatch/, false],
  ["10 uploadPackageImportFile", /api\.uploadPackageImportFile/, false],
  ["10 analyzePackageImportBatch", /api\.analyzePackageImportBatch/, false],
  ["10 storedPackageBatches", /storedPackageBatches/, false],
  ["11 no setInterval", /setInterval\(/, true],
  ["12 submittedImportTexts", /submittedImportTexts/, false],
  ["12 fullySuccessful", /fullySuccessful/, false],
  ["13 duplicateOnly", /const duplicateOnly =/, false],
  ["13 已去掉重复链接", /已去掉 \$\{created\.duplicate_count\} 条重复链接/, false],
  ["13 没有新的作品需要预检", /没有新的作品需要预检/, false],
  ["14 item.error_code && <code>", /item\.error_code && <code>/, false],
  ["14 上传本地补件", /上传本地补件/, false],
  ["18 可选解析 Cookie", /可选解析 Cookie/, false],
  ["20 import-overview", /className="import-overview"/, false],
  ["21 导入已经下载好的视频", /导入已经下载好的视频/, false],
  ["21 LOCAL_VIDEO_LIMIT", /const LOCAL_VIDEO_LIMIT = 10/, false],
  ["22 import-workspace stack hidden", /<section className="import-workspace stack" hidden=\{hidden\}>/, false],
  ["37 待补件 <em>", /待补件 <em>\{summary\.supplement_count \?\? 0\}<\/em>/, false],
];

let fixedByBlob = 0;
let stillFailing = [];
for (const [name, re, neg] of assertions) {
  const oldHit = re.test(OLD);
  const newHit = re.test(NEW);
  const oldOk = neg ? !oldHit : oldHit;
  const newOk = neg ? !newHit : newHit;
  if (!oldOk && newOk) fixedByBlob++;
  if (!newOk) stillFailing.push(name);
}

console.log(`旧 blob(3 个文件)失败的断言: ${assertions.filter(([n, re, neg]) => (neg ? !re.test(OLD) : re.test(OLD)) === false).length}`);
console.log(`换新 blob(6 个文件)后仍失败: ${stillFailing.length}`);
for (const n of stillFailing) console.log("   ✗ " + n);
console.log(`\n仅靠补齐 blob 就能救回: ${fixedByBlob} 条`);
