#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { tmpdir } from "node:os";

const ROOT = path.resolve(process.cwd());
const STATIC_CTA_SEC = 3;

function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i += 1) {
    const k = argv[i];
    if (!k.startsWith("--")) { o.poster = k; continue; }
    o[k.slice(2)] = argv[++i];
  }
  return o;
}

function ffmpeg(args, opts = {}) {
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: "inherit", ...opts });
}

function visualClip(input, output, duration, isImage) {
  const args = [];
  if (isImage) args.push("-loop", "1");
  args.push("-i", input, "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black", "-an");
  args.push("-t", String(duration), "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", output);
  ffmpeg(args);
}

function mediaDuration(input) {
  return Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", input], { encoding: "utf8" }).trim());
}

const opts = parseArgs(process.argv.slice(2));
const poster = path.resolve(opts.poster);
const cta = path.resolve(opts.cta);
const music = opts.music ? path.resolve(opts.music) : null;
const voice = opts.voice ? path.resolve(opts.voice) : null;
const hookVideo = opts["hook-video"] ? path.resolve(opts["hook-video"]) : null;
const keypadCode = opts["keypad-code"] || "2252960";
const DTMF = { 0:[941,1336],1:[697,1209],2:[697,1336],3:[697,1477],4:[770,1209],5:[770,1336],6:[770,1477],7:[852,1209],8:[852,1336],9:[852,1477] };
const slot = 0.16;
const tone = 0.09;
const kpTerms = keypadCode.split("").map((d, i) => {
  const [f1, f2] = DTMF[d] || DTMF[0];
  return `(sin(2*PI*${f1}*t)+sin(2*PI*${f2}*t))*between(t\\,${(i * slot).toFixed(2)}\\,${(i * slot + tone).toFixed(2)})`;
});
const kpExpr = `0.3*(${kpTerms.join("+")})`;
const kpDur = (keypadCode.length * slot).toFixed(2);
const modules = (opts.modules || "").split(",").filter(Boolean).map((p) => path.resolve(p));
const randomBefore = (opts["random-before"] || "").split(",").filter(Boolean).map((p) => path.resolve(p));
let midModules = randomBefore.length ? [...modules] : modules;
if (randomBefore.length) {
  const picked = randomBefore[Math.floor(Math.random() * randomBefore.length)];
  midModules = [picked, ...modules];
  console.log(`[随机中段] ${path.basename(picked)}`);
}
const output = path.resolve(opts.output);
const POSTER_SEC = Number(opts["poster-sec"]) || 4;
for (const [label, p] of [["poster", poster], ["cta", cta], ...midModules.map((m, i) => [`module${i}`, m])]) {
  if (!existsSync(p)) throw new Error(`${label} not found: ${p}`);
}
if (hookVideo && !existsSync(hookVideo)) throw new Error(`hook-video not found: ${hookVideo}`);
if (music && !existsSync(music)) throw new Error(`music not found: ${music}`);
if (voice && !existsSync(voice)) throw new Error(`voice not found: ${voice}`);

// CTA may be a static image (default) or an animated video clip (e.g. motion CTA from lyz/cta).
const ctaIsImage = !/\.(mp4|mov|webm|mkv)$/i.test(cta);
const middleDurations = midModules.map(mediaDuration);
const middleSec = middleDurations.reduce((sum, seconds) => sum + seconds, 0);
const ctaSec = ctaIsImage ? STATIC_CTA_SEC : mediaDuration(cta);
const total = POSTER_SEC + middleSec + ctaSec;
const ctaStart = POSTER_SEC + middleSec;
const keypad = !!opts.keypad || /downloader/i.test(path.basename(midModules[0] || ""));
const work = mkdtempSync(path.join(tmpdir(), "jaguartv-compose-"));

try {
  const posterClip = path.join(work, "00-poster.mp4");
  const concatList = path.join(work, "concat.txt");
  const videoOnly = path.join(work, "video.mp4");
  if (hookVideo) visualClip(hookVideo, posterClip, POSTER_SEC, false);
  else visualClip(poster, posterClip, POSTER_SEC, true);

  const files = [posterClip];
  midModules.forEach((m, i) => {
    const clip = path.join(work, `module-${i}.mp4`);
    visualClip(m, clip, middleDurations[i], false);
    files.push(clip);
  });
  const ctaClip = path.join(work, "cta.mp4");
  visualClip(cta, ctaClip, ctaSec, ctaIsImage);
  files.push(ctaClip);

  writeFileSync(concatList, files.map((f) => `file '${f.replaceAll("'", "'\\''")}'`).join("\n") + "\n");
  ffmpeg(["-f", "concat", "-safe", "0", "-i", concatList, "-an", "-t", String(total), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "30", "-pix_fmt", "yuv420p", videoOnly]);

  const hasMusic = music && existsSync(music);
  const hasVoice = voice && existsSync(voice);
  const bodyVol = 0.3;
  const ctaVol = 0.12;
  if (hasMusic) {
    const inputs = [
      "-i", videoOnly,
      "-stream_loop", "-1", "-i", music,
    ];
    let nextIdx = 2;
    const filters = [`[1:a]atrim=0:${total},asetpts=N/SR/TB,volume='if(gte(t,${ctaStart}),${ctaVol},${bodyVol})':eval=frame[bg]`];
    let amixIn = `[bg]`;
    let inputsN = 1;
    if (keypad) {
      inputs.push("-f", "lavfi", "-i", `aevalsrc=${kpExpr}:s=48000:d=${kpDur}`);
      filters.push(`[${nextIdx}:a]adelay=${Math.round((POSTER_SEC + 0.2) * 1000)}|${Math.round((POSTER_SEC + 0.2) * 1000)},volume=0.85[kp]`);
      amixIn += `[kp]`;
      inputsN += 1;
      nextIdx += 1;
    }
    if (hasVoice) {
      inputs.push("-i", voice);
      filters.push(`[${nextIdx}:a]atrim=0:${ctaSec},asetpts=N/SR/TB,adelay=${Math.round(ctaStart * 1000)}:all=1,volume=1.15[vo]`);
      amixIn += `[vo]`;
      inputsN += 1;
    }
    filters.push(`${amixIn}amix=inputs=${inputsN}:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=7,alimiter=limit=0.8:attack=5:release=50[a]`);
    ffmpeg([...inputs, "-filter_complex", filters.join(";"), "-map", "0:v", "-map", "[a]", "-t", String(total), "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", output]);
  } else {
    ffmpeg(["-i", videoOnly, "-map", "0:v", "-an", "-t", String(total), "-c:v", "copy", "-movflags", "+faststart", output]);
  }
  console.log(output);
} finally {
  rmSync(work, { recursive: true, force: true });
}
