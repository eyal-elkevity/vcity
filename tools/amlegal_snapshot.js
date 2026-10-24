async (titlesFilter = null, onSection = () => {}) => {
  const codeUuid = "14e4150b-fe19-4c1a-9934-7cd76279dad1";
  const base = "/codes/corona/latest/corona_ca";
  const politeDelayMs = 300;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const getJson = async (path) => {
    for (let attempt = 0; attempt < 6; attempt++) {
      await sleep(politeDelayMs * 2 ** attempt);
      const response = await fetch(path).catch(() => null);
      if (response?.ok) return response.json();
    }
    throw new Error(`failed ${path}`);
  };
  const tocChildren = async (docId) => {
    const chain = await getJson(`/api/toc-chain/${codeUuid}/${docId}/`);
    return chain.find((node) => node.doc_id === docId)?.children ?? [];
  };
  const pool = async (items, limit, worker) => {
    const results = new Array(items.length);
    let next = 0;
    await Promise.all(Array.from({ length: limit }, async () => {
      while (next < items.length) {
        const index = next++;
        results[index] = await worker(items[index]);
      }
    }));
    return results;
  };
  const walk = async (node) => {
    if (!node.has_children) {
      const renderDoc = node.orig_doc_id ?? node.doc_id;
      const record = await getJson(`/api/render-section/corona/latest/corona_ca/${renderDoc}/${node.orig_doc_idx}/`);
      onSection(node.title);
      return { doc_id: node.doc_id, title: node.title, url: `https://codelibrary.amlegal.com${base}/${node.doc_id}`, html: record.html };
    }
    const children = await tocChildren(node.doc_id);
    return { doc_id: node.doc_id, title: node.title, children: await pool(children, 2, walk) };
  };
  const overview = new DOMParser().parseFromString(await (await fetch("/codes/corona/latest/overview")).text(), "text/html");
  const titles = [...new Map([...overview.querySelectorAll(`a[href*="${base}/"]`)]
    .map((a) => ({ title: a.textContent.trim(), doc_id: a.getAttribute("href").split("/").pop() }))
    .filter((t) => /^TITLE \d+/.test(t.title) && !/RESERVED/.test(t.title))
    .filter((t) => !titlesFilter || titlesFilter.includes(t.title.split(" ")[1]))
    .map((t) => [t.doc_id, t])).values()];
  const tree = [];
  for (const title of titles) tree.push(await walk({ ...title, has_children: true }));
  return { source: "https://codelibrary.amlegal.com/codes/corona/latest/overview", code_uuid: codeUuid, saved_on: new Date().toISOString().slice(0, 10), titles: tree };
}
