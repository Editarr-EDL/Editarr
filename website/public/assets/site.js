const toggle = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#navigation');
function closeMenu() {
  toggle?.setAttribute('aria-expanded', 'false');
  if (navigation) navigation.dataset.open = 'false';
}
toggle?.addEventListener('click', () => {
  const open = toggle.getAttribute('aria-expanded') !== 'true';
  toggle.setAttribute('aria-expanded', String(open));
  navigation.dataset.open = String(open);
});
navigation?.addEventListener('click', (event) => { if (event.target.closest('a')) closeMenu(); });
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && toggle?.getAttribute('aria-expanded') === 'true') { closeMenu(); toggle.focus(); }
});

/** Opens screenshot links in an in-page modal viewer; the link stays a no-JS fallback. */
function setUpLightbox() {
  const shots = document.querySelectorAll('a.shot');
  if (!shots.length || typeof HTMLDialogElement !== 'function') return;
  const dialog = document.createElement('dialog');
  dialog.className = 'lightbox';
  dialog.setAttribute('aria-labelledby', 'lightbox-caption');
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'lightbox-close';
  close.setAttribute('aria-label', 'Close screenshot');
  close.textContent = '×';
  const figure = document.createElement('figure');
  const image = document.createElement('img');
  image.decoding = 'async';
  const caption = document.createElement('figcaption');
  caption.id = 'lightbox-caption';
  figure.append(image, caption);
  dialog.append(close, figure);
  document.body.append(dialog);
  let opener = null;

  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => { if (event.target === dialog || event.target === figure) dialog.close(); });
  dialog.addEventListener('close', () => {
    document.documentElement.classList.remove('lightbox-open');
    image.removeAttribute('src');
    opener?.focus();
    opener = null;
  });
  shots.forEach((shot) => shot.addEventListener('click', (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const thumbnail = shot.querySelector('img');
    const title = shot.closest('figure')?.querySelector('figcaption') ?? shot.closest('.tour-row')?.querySelector('h3');
    opener = shot;
    image.src = shot.getAttribute('href');
    image.alt = thumbnail?.alt ?? '';
    caption.textContent = title?.textContent ?? 'Screenshot';
    document.documentElement.classList.add('lightbox-open');
    dialog.showModal();
    close.focus();
  }));
}
setUpLightbox();
