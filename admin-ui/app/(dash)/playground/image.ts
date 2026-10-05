/** File/Blob gambar → data URL; dikecilkan ke sisi terpanjang `max` px (JPEG 0.88) supaya upload ringan. */
export async function toDataUrl(file: Blob, max = 1600): Promise<string> {
  const raw = await new Promise<string>((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result));
    r.onerror = () => rej(r.error);
    r.readAsDataURL(file);
  });
  if (file.type === "image/gif") return raw; // jangan rusak animasi
  const img = await new Promise<HTMLImageElement>((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = () => rej(new Error("Gambar tidak bisa dibaca"));
    i.src = raw;
  });
  const scale = Math.min(1, max / Math.max(img.width, img.height));
  if (scale === 1 && file.size < 1.5 * 1024 * 1024) return raw;
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * scale);
  c.height = Math.round(img.height * scale);
  c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.88);
}

/** URL absolut dari backend (http://host:8168/v1/media/x.jpg) → path relatif lewat proxy Next. */
export const viaProxy = (u: string) => { try { const x = new URL(u, location.origin); return x.pathname + x.search; } catch { return u; } };
