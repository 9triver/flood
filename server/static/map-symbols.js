/**
 * Domain map symbols derived from selected Lucide 1.26.0 icons.
 * @license ISC; see /vendor/lucide-LICENSE.txt.
 */
(function attachFloodMapSymbols(global) {
  "use strict";

  const symbolNodes = {
    'building-2': [
  ["path", { d: "M10 12h4" }],
  ["path", { d: "M10 8h4" }],
  ["path", { d: "M14 21v-3a2 2 0 0 0-4 0v3" }],
  ["path", { d: "M6 10H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-2" }],
  ["path", { d: "M6 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16" }]
],
    'cloud-lightning': [
  ["path", { d: "M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973" }],
  ["path", { d: "m13 12-3 5h4l-3 5" }]
],
    'cloud-sun': [
  ["path", { d: "M12 2v2" }],
  ["path", { d: "m4.93 4.93 1.41 1.41" }],
  ["path", { d: "M20 12h2" }],
  ["path", { d: "m19.07 4.93-1.41 1.41" }],
  ["path", { d: "M15.947 12.65a4 4 0 0 0-5.925-4.128" }],
  ["path", { d: "M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z" }]
],
    'dam': [
  ["path", { d: "M11 11.31c1.17.56 1.54 1.69 3.5 1.69 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" }],
  ["path", { d: "M11.75 18c.35.5 1.45 1 2.75 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" }],
  ["path", { d: "M2 10h4" }],
  ["path", { d: "M2 14h4" }],
  ["path", { d: "M2 18h4" }],
  ["path", { d: "M2 6h4" }],
  ["path", { d: "M7 3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1L10 4a1 1 0 0 0-1-1z" }]
],
    'gauge': [
  ["path", { d: "m12 14 4-4" }],
  ["path", { d: "M3.34 19a10 10 0 1 1 17.32 0" }]
],
    'hospital': [
  ["path", { d: "M12 7v4" }],
  ["path", { d: "M14 21v-3a2 2 0 0 0-4 0v3" }],
  ["path", { d: "M14 9h-4" }],
  ["path", { d: "M18 11h2a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h2" }],
  ["path", { d: "M18 21V5a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16" }]
],
    'house-heart': [
  [
    "path",
    {
      d: "M8.62 13.8A2.25 2.25 0 1 1 12 10.836a2.25 2.25 0 1 1 3.38 2.966l-2.626 2.856a.998.998 0 0 1-1.507 0z"
    }
  ],
  [
    "path",
    {
      d: "M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
    }
  ]
],
    'landmark': [
  ["path", { d: "M10 18v-7" }],
  [
    "path",
    { d: "M11.119 2.205a2 2 0 0 1 1.762 0l7.84 3.846A.5.5 0 0 1 20.5 7h-17a.5.5 0 0 1-.22-.949z" }
  ],
  ["path", { d: "M14 18v-7" }],
  ["path", { d: "M18 18v-7" }],
  ["path", { d: "M3 22h18" }],
  ["path", { d: "M6 18v-7" }]
],
    'layers': [
  [
    "path",
    {
      d: "M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"
    }
  ],
  ["path", { d: "M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12" }],
  ["path", { d: "M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17" }]
],
    'map': [
  [
    "path",
    {
      d: "M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"
    }
  ],
  ["path", { d: "M15 5.764v15" }],
  ["path", { d: "M9 3.236v15" }]
],
    'map-pin': [
  [
    "path",
    {
      d: "M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"
    }
  ],
  ["circle", { cx: "12", cy: "10", r: "3" }]
],
    'radio-tower': [
  ["path", { d: "M4.9 16.1C1 12.2 1 5.8 4.9 1.9" }],
  ["path", { d: "M7.8 4.7a6.14 6.14 0 0 0-.8 7.5" }],
  ["circle", { cx: "12", cy: "9", r: "2" }],
  ["path", { d: "M16.2 4.8c2 2 2.26 5.11.8 7.47" }],
  ["path", { d: "M19.1 1.9a9.96 9.96 0 0 1 0 14.1" }],
  ["path", { d: "M9.5 18h5" }],
  ["path", { d: "m8 22 4-11 4 11" }]
],
    route: [
      ["circle", { cx: "6", cy: "19", r: "3" }],
      ["path", { d: "M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" }],
      ["circle", { cx: "18", cy: "5", r: "3" }],
    ],
    'school': [
  ["path", { d: "M14 21v-3a2 2 0 0 0-4 0v3" }],
  ["path", { d: "M18 4.933V21" }],
  ["path", { d: "m4 6 7.106-3.79a2 2 0 0 1 1.788 0L20 6" }],
  [
    "path",
    {
      d: "m6 11-3.52 2.147a1 1 0 0 0-.48.854V19a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-5a1 1 0 0 0-.48-.853L18 11"
    }
  ],
  ["path", { d: "M6 4.933V21" }],
  ["circle", { cx: "12", cy: "9", r: "2" }]
],
    'triangle-alert': [
  ["path", { d: "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" }],
  ["path", { d: "M12 9v4" }],
  ["path", { d: "M12 17h.01" }]
],
    'users': [
  ["path", { d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }],
  ["path", { d: "M16 3.128a4 4 0 0 1 0 7.744" }],
  ["path", { d: "M22 21v-2a4 4 0 0 0-3-3.87" }],
  ["circle", { cx: "9", cy: "7", r: "4" }]
],
    'waves-horizontal': [
  ["path", { d: "M2 12q2.5 2 5 0t5 0 5 0 5 0" }],
  ["path", { d: "M2 19q2.5 2 5 0t5 0 5 0 5 0" }],
  ["path", { d: "M2 5q2.5 2 5 0t5 0 5 0 5 0" }]
],
    bridge: [
      ["path", { d: "M3 19h18" }],
      ["path", { d: "M5 19V9" }],
      ["path", { d: "M19 19V9" }],
      ["path", { d: "M5 13h14" }],
      ["path", { d: "M5 9c3.5 0 3.5 4 7 4s3.5-4 7-4" }],
      ["path", { d: "M9 19v-6" }],
      ["path", { d: "M15 19v-6" }],
    ],
  };

  function escapeAttribute(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll('"', "&quot;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function render(symbolName) {
    const nodes = symbolNodes[symbolName] || symbolNodes["map-pin"];
    const body = nodes.map(([tag, attributes]) => {
      const attrs = Object.entries(attributes || {})
        .map(([name, value]) => `${name}="${escapeAttribute(value)}"`)
        .join(" ");
      return `<${tag}${attrs ? ` ${attrs}` : ""}></${tag}>`;
    }).join("");
    return `<svg class="object-symbol-glyph" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`;
  }

  global.FloodMapSymbols = Object.freeze({
    render,
    names: Object.freeze(Object.keys(symbolNodes)),
  });
})(window);
