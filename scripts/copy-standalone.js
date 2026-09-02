const fs = require('fs');
const path = require('path');

function copyFolderSync(from, to) {
  if (!fs.existsSync(from)) return;
  if (!fs.existsSync(to)) fs.mkdirSync(to, { recursive: true });
  fs.readdirSync(from).forEach(element => {
    const stat = fs.lstatSync(path.join(from, element));
    if (stat.isFile()) {
      fs.copyFileSync(path.join(from, element), path.join(to, element));
    } else if (stat.isDirectory()) {
      copyFolderSync(path.join(from, element), path.join(to, element));
    }
  });
}

try {
  const standaloneNext = path.join(__dirname, '..', '.next', 'standalone', '.next');
  const staticSrc = path.join(__dirname, '..', '.next', 'static');
  const staticDest = path.join(standaloneNext, 'static');
  copyFolderSync(staticSrc, staticDest);

  const publicSrc = path.join(__dirname, '..', 'public');
  const publicDest = path.join(__dirname, '..', '.next', 'standalone', 'public');
  copyFolderSync(publicSrc, publicDest);
  console.log('Successfully copied standalone assets.');
} catch (err) {
  console.warn('Standalone copy warning (non-fatal):', err.message);
}
