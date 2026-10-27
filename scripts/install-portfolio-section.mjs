import {cp, lstat, readFile, readdir, realpath, rename, rm, writeFile} from 'node:fs/promises';
import {createHash, randomUUID} from 'node:crypto';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {verifyHostingPackage} from './verify-hosting.mjs';

const PROJECT = 'nqsieptekarxxmqrgmdy';
const MARKER = '.xrbench-section.json';

async function exists(file) {
  try {await lstat(file); return true;} catch (error) {if (error.code === 'ENOENT') return false; throw error;}
}

async function hashes(directory, prefix = '') {
  const result = {};
  for (const name of (await readdir(directory)).sort()) {
    const relative = prefix + name, full = path.join(directory, name), info = await lstat(full);
    if (info.isSymbolicLink()) throw Error('A managed section must not contain symlinks.');
    if (info.isDirectory()) Object.assign(result, await hashes(full, relative + '/'));
    else if (info.isFile()) result[relative] = createHash('sha256').update(await readFile(full)).digest('hex');
    else throw Error('A managed section must contain only ordinary files.');
  }
  return result;
}

export async function installPortfolioSection({directory, portfolio, check = false}) {
  if (!directory || !portfolio) throw Error('Supply both the hosted package and portfolio checkout.');
  const source = await realpath(directory), root = await realpath(portfolio);
  const verified = await verifyHostingPackage(source, {projectRef: PROJECT, sitePath: '/xrbench/'});
  const publicDirectory = path.join(root, 'public'), destination = path.join(publicDirectory, 'xrbench');
  if (source === destination || source.startsWith(destination + path.sep) || destination.startsWith(source + path.sep))
    throw Error('The package source must be separate from the installed website section.');
  const publicInfo = await lstat(publicDirectory);
  if (!publicInfo.isDirectory() || publicInfo.isSymbolicLink()) throw Error('The portfolio public directory must be an ordinary directory.');
  const markerPath = path.join(root, MARKER), installed = await exists(destination), marked = await exists(markerPath);
  let previousMarker = null;
  if (installed !== marked) throw Error('Refusing an unmanaged or incomplete xrBench installation.');
  if (installed) {
    const info = await lstat(destination), markerInfo = await lstat(markerPath);
    if (!info.isDirectory() || info.isSymbolicLink() || !markerInfo.isFile() || markerInfo.isSymbolicLink())
      throw Error('Refusing an invalid managed section.');
    previousMarker = await readFile(markerPath);
    let marker;
    try {marker = JSON.parse(previousMarker);} catch {throw Error('The xrBench management marker is invalid.');}
    if (marker.schema_version !== 1 || marker.project_ref !== PROJECT || marker.directory !== 'public/xrbench' ||
        JSON.stringify(await hashes(destination)) !== JSON.stringify(marker.hashes))
      throw Error('Managed assets changed locally; preserve or review those edits before updating.');
  }
  const result = {checked: true, installed: false, project_ref: PROJECT,
    site_origin: verified.config.siteOrigin, site_path: '/xrbench/', destination};
  if (check) return result;
  const id = randomUUID(), staging = path.join(publicDirectory, '.xrbench-stage-' + id);
  const backup = path.join(publicDirectory, '.xrbench-backup-' + id), markerTemp = path.join(root, MARKER + '.' + id);
  let switched = false, backedUp = false;
  try {
    await cp(source, staging, {recursive: true, errorOnExist: true, force: false, dereference: false});
    // Revalidate the actual staged tree; a copy can never publish a symlink or
    // incidental file that appeared after the source's first validation.
    await verifyHostingPackage(staging, {projectRef: PROJECT, sitePath: '/xrbench/'});
    const marker = {schema_version: 1, project_ref: PROJECT, directory: 'public/xrbench',
      site_origin: verified.config.siteOrigin, hashes: await hashes(staging)};
    await writeFile(markerTemp, JSON.stringify(marker, null, 2) + '\n', {flag: 'wx', mode: 0o600});
    if (installed) {await rename(destination, backup); backedUp = true;}
    await rename(staging, destination); switched = true;
    await rename(markerTemp, markerPath);
  } catch (error) {
    if (switched) await rm(destination, {recursive: true, force: true});
    if (backedUp) await rename(backup, destination);
    if (previousMarker) await writeFile(markerPath, previousMarker, {mode: 0o600});
    else if (switched) await rm(markerPath, {force: true});
    throw error;
  } finally {
    await rm(staging, {recursive: true, force: true});
    await rm(markerTemp, {force: true});
  }
  // Once the directory and marker have both committed, cleanup failure must
  // never trigger a rollback that could destroy the now-valid installation.
  if (backedUp) {
    try {await rm(backup, {recursive: true});}
    catch {return {...result, installed: true, retained_backup: backup};}
  }
  return {...result, installed: true};
}

async function main() {
  const args = process.argv.slice(2), options = {};
  while (args.length) {
    const arg = args.shift();
    if (arg === '--check' || arg === '--dry-run') options.check = true;
    else if (['--dist', '--portfolio'].includes(arg) && args.length)
      options[arg === '--dist' ? 'directory' : 'portfolio'] = args.shift();
    else throw Error('Usage: install-portfolio-section.mjs --dist HOSTED_PACKAGE --portfolio CHECKOUT [--check]');
  }
  const result = await installPortfolioSection(options);
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href)
  main().catch(error => {console.error('Installation refused: ' + error.message); process.exitCode = 1;});
