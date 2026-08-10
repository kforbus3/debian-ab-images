// Where a picked folder's files land in the image.
//
// This is the one place in the app that turns paths chosen in a browser into
// destinations for a write endpoint. The server refuses anything escaping
// overlay.d regardless, so nothing here is the last line of defence -- but a
// mis-join still puts a fleet's configuration at a path nobody asked for, and
// that is the kind of mistake found by a machine behaving oddly rather than by
// an error.
//
// Plain node, no test framework: the project has none for the frontend, and
// adding one to check two pure functions is a worse trade than this.
//
//   node --experimental-strip-types src/lib/overlayUpload.test.mjs
// or, as CI runs it, after esbuild has turned the .ts into .mjs.
import { joinImagePath, planDirUpload, MAX_DIR_FILES } from "./overlayUpload.built.mjs";

let ok = 0, fail = 0;
const check = (name, cond, extra = "") => {
  if (cond) { ok++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name} ${extra}`); }
};

// A File stand-in: the planner only reads webkitRelativePath, name and size.
const f = (rel, size = 10) => ({ name: rel.split("/").pop(), size, webkitRelativePath: rel });

console.log("== joining a prefix and a relative path ==");
check("root prefix", joinImagePath("/", "etc/hosts") === "/etc/hosts");
check("nested prefix", joinImagePath("/opt/app", "bin/run") === "/opt/app/bin/run");
check("trailing slash on the prefix", joinImagePath("/opt/", "bin/run") === "/opt/bin/run");
check("missing leading slash is added", joinImagePath("opt", "bin/run") === "/opt/bin/run");
check("empty prefix means root", joinImagePath("", "etc/hosts") === "/etc/hosts");
check("repeated slashes collapse", joinImagePath("//opt//", "//bin//run") === "/opt/bin/run");
check("backslashes are normalised", joinImagePath("\\opt", "bin/run") === "/opt/bin/run");

console.log("== the folder's own name is included or stripped, as asked ==");
// Picking "etc" containing "hosts" gives webkitRelativePath "etc/hosts".
let plan = planDirUpload([f("etc/hosts"), f("etc/ssh/sshd_config")], "/", true);
check("included: /etc/hosts", plan.send[0].path === "/etc/hosts", plan.send[0].path);
check("included: nested", plan.send[1].path === "/etc/ssh/sshd_config", plan.send[1].path);

plan = planDirUpload([f("myfolder/etc/hosts")], "/", false);
check("stripped: the wrapper folder disappears", plan.send[0].path === "/etc/hosts", plan.send[0].path);

plan = planDirUpload([f("myfolder/hosts")], "/etc", false);
check("stripped, with a prefix", plan.send[0].path === "/etc/hosts", plan.send[0].path);

// Stripping when the file sits directly in the chosen folder leaves nothing to
// send; it must be skipped rather than silently becoming the prefix itself.
plan = planDirUpload([f("solo.txt")], "/etc", false);
check("stripping a single-segment path skips it", plan.send.length === 0 && plan.skipped.length === 1,
      JSON.stringify(plan.send));

console.log("== droppings are skipped, and counted ==");
plan = planDirUpload([
  f("etc/hosts"), f("etc/.DS_Store"), f(".DS_Store"),
  f("etc/.git/config"), f("etc/.gitkeep"), f("etc/Thumbs.db"),
], "/", true);
check("only the real file is sent", plan.send.length === 1, JSON.stringify(plan.send.map(s => s.path)));
check("the rest are reported, not dropped in silence", plan.skipped.length === 5, String(plan.skipped.length));
check("a .git tree does not reach the image",
      !plan.send.some((s) => s.path.includes("/.git/")));

console.log("== a name containing .git is not mistaken for a .git directory ==");
plan = planDirUpload([f("etc/gitconfig"), f("etc/mygit/file"), f("etc/.gitignore")], "/", true);
check("gitconfig kept", plan.send.some((s) => s.path === "/etc/gitconfig"));
check("mygit/ kept", plan.send.some((s) => s.path === "/etc/mygit/file"));
check(".gitignore kept (only .gitkeep is dropped)", plan.send.some((s) => s.path === "/etc/.gitignore"),
      JSON.stringify(plan.skipped));

console.log("== sizes are totalled for the preview ==");
plan = planDirUpload([f("a/x", 100), f("a/y", 250), f("a/.DS_Store", 9999)], "/", true);
check("skipped files do not count toward the total", plan.bytes === 350, String(plan.bytes));

console.log("== every produced path is absolute ==");
plan = planDirUpload([f("a/b/c.txt"), f("a/d.txt")], "opt//sub/", true);
check("all absolute", plan.send.every((s) => s.path.startsWith("/")),
      JSON.stringify(plan.send.map(s => s.path)));
check("no doubled slashes", plan.send.every((s) => !s.path.includes("//")),
      JSON.stringify(plan.send.map(s => s.path)));

console.log("== the guard rails exist ==");
check("file-count limit is set", MAX_DIR_FILES > 0 && MAX_DIR_FILES <= 5000, String(MAX_DIR_FILES));

console.log(`\n${ok} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
