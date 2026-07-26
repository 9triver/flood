/**
 * Selected Lucide 1.26.0 icons for the flood frontend.
 * @license ISC; see lucide-LICENSE.txt.
 */
(function attachLucide(global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const iconNodes = {
  'activity': [
  [
    "path",
    {
      d: "M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"
    }
  ]
],
  'brain-circuit': [
  [
    "path",
    { d: "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" }
  ],
  ["path", { d: "M9 13a4.5 4.5 0 0 0 3-4" }],
  ["path", { d: "M6.003 5.125A3 3 0 0 0 6.401 6.5" }],
  ["path", { d: "M3.477 10.896a4 4 0 0 1 .585-.396" }],
  ["path", { d: "M6 18a4 4 0 0 1-1.967-.516" }],
  ["path", { d: "M12 13h4" }],
  ["path", { d: "M12 18h6a2 2 0 0 1 2 2v1" }],
  ["path", { d: "M12 8h8" }],
  ["path", { d: "M16 8V5a2 2 0 0 1 2-2" }],
  ["circle", { cx: "16", cy: "13", r: ".5" }],
  ["circle", { cx: "18", cy: "3", r: ".5" }],
  ["circle", { cx: "20", cy: "21", r: ".5" }],
  ["circle", { cx: "20", cy: "8", r: ".5" }]
],
  'chevron-down': [["path", { d: "m6 9 6 6 6-6" }]],
  'check': [["path", { d: "M20 6 9 17l-5-5" }]],
  'copy': [
  ["rect", { width: "14", height: "14", x: "8", y: "8", rx: "2", ry: "2" }],
  ["path", { d: "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" }]
],
  'cloud-drizzle': [
  ["path", { d: "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" }],
  ["path", { d: "M8 19v1" }],
  ["path", { d: "M8 14v1" }],
  ["path", { d: "M16 19v1" }],
  ["path", { d: "M16 14v1" }],
  ["path", { d: "M12 21v1" }],
  ["path", { d: "M12 16v1" }]
],
  'cloud-lightning': [
  ["path", { d: "M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973" }],
  ["path", { d: "m13 12-3 5h4l-3 5" }]
],
  'cloud-rain': [
  ["path", { d: "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" }],
  ["path", { d: "M16 14v6" }],
  ["path", { d: "M8 14v6" }],
  ["path", { d: "M12 16v6" }]
],
  'cloud-rain-wind': [
  ["path", { d: "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" }],
  ["path", { d: "m9.2 22 3-7" }],
  ["path", { d: "m9 13-3 7" }],
  ["path", { d: "m17 13-3 7" }]
],
  'cloud-sun': [
  ["path", { d: "M12 2v2" }],
  ["path", { d: "m4.93 4.93 1.41 1.41" }],
  ["path", { d: "M20 12h2" }],
  ["path", { d: "m19.07 4.93-1.41 1.41" }],
  ["path", { d: "M15.947 12.65a4 4 0 0 0-5.925-4.128" }],
  ["path", { d: "M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z" }]
],
  'database': [
  ["ellipse", { cx: "12", cy: "5", rx: "9", ry: "3" }],
  ["path", { d: "M3 5v14a9 3 0 0 0 18 0V5" }],
  ["path", { d: "M3 12a9 3 0 0 0 18 0" }]
],
  'gauge': [
  ["path", { d: "m12 14 4-4" }],
  ["path", { d: "M3.34 19a10 10 0 1 1 17.32 0" }]
],
  'file-text': [
  ["path", { d: "M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" }],
  ["polyline", { points: "14 2 14 8 20 8" }],
  ["line", { x1: "16", x2: "8", y1: "13", y2: "13" }],
  ["line", { x1: "16", x2: "8", y1: "17", y2: "17" }],
  ["line", { x1: "10", x2: "8", y1: "9", y2: "9" }]
],
  'file-pen-line': [
  ["path", { d: "m18 5-2.414-2.414A2 2 0 0 0 14.172 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2" }],
  ["path", { d: "M21.378 12.626a1 1 0 0 0-3.004-3.004l-4.01 4.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z" }],
  ["path", { d: "M8 18h1" }]
],
  'file-spreadsheet': [
  ["path", { d: "M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" }],
  ["polyline", { points: "14 2 14 8 20 8" }],
  ["path", { d: "M8 13h2" }],
  ["path", { d: "M8 17h2" }],
  ["path", { d: "M14 13h2" }],
  ["path", { d: "M14 17h2" }]
],
  'grip-horizontal': [
  ["circle", { cx: "12", cy: "9", r: "1" }],
  ["circle", { cx: "19", cy: "9", r: "1" }],
  ["circle", { cx: "5", cy: "9", r: "1" }],
  ["circle", { cx: "12", cy: "15", r: "1" }],
  ["circle", { cx: "19", cy: "15", r: "1" }],
  ["circle", { cx: "5", cy: "15", r: "1" }]
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
  'list-tree': [
  ["path", { d: "M8 5h13" }],
  ["path", { d: "M13 12h8" }],
  ["path", { d: "M13 19h8" }],
  ["path", { d: "M3 10a2 2 0 0 0 2 2h3" }],
  ["path", { d: "M3 5v12a2 2 0 0 0 2 2h3" }]
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
  'messages-square': [
  [
    "path",
    {
      d: "M16 10a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 14.286V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"
    }
  ],
  [
    "path",
    {
      d: "M20 9a2 2 0 0 1 2 2v10.286a.71.71 0 0 1-1.212.502l-2.202-2.202A2 2 0 0 0 17.172 19H10a2 2 0 0 1-2-2v-1"
    }
  ]
],
  'panel-bottom': [
  ["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }],
  ["path", { d: "M3 15h18" }]
],
  'panel-bottom-close': [
  ["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }],
  ["path", { d: "M3 15h18" }],
  ["path", { d: "m15 8-3 3-3-3" }]
],
  'panel-bottom-open': [
  ["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }],
  ["path", { d: "M3 15h18" }],
  ["path", { d: "m9 10 3-3 3 3" }]
],
  'panel-right-open': [
  ["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }],
  ["path", { d: "M15 3v18" }],
  ["path", { d: "m10 15-3-3 3-3" }]
],
  'pause': [
  ["rect", { x: "14", y: "3", width: "5", height: "18", rx: "1" }],
  ["rect", { x: "5", y: "3", width: "5", height: "18", rx: "1" }]
],
  'picture-in-picture-2': [
  ["path", { d: "M21 9V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10c0 1.1.9 2 2 2h4" }],
  ["rect", { width: "10", height: "7", x: "12", y: "13", rx: "2" }]
],
  'play': [
  [
    "path",
    { d: "M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z" }
  ]
],
  'step-forward': [
  ["path", { d: "M10.029 4.285A2 2 0 0 0 7 6v12a2 2 0 0 0 3.029 1.715l9.997-5.998a2 2 0 0 0 .003-3.432z" }],
  ["path", { d: "M3 4v16" }]
],
  'skip-forward': [
  ["path", { d: "M5 4a2 2 0 0 1 3.008-1.728l9.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 18z" }],
  ["path", { d: "M19 5v14" }]
],
  'upload': [
  ["path", { d: "M12 3v12" }],
  ["path", { d: "m17 8-5-5-5 5" }],
  ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }]
],
  'rotate-ccw': [
  ["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }],
  ["path", { d: "M3 3v5h5" }]
],
  'satellite': [
  [
    "path",
    {
      d: "m13.5 6.5-3.148-3.148a1.205 1.205 0 0 0-1.704 0L6.352 5.648a1.205 1.205 0 0 0 0 1.704L9.5 10.5"
    }
  ],
  ["path", { d: "M16.5 7.5 19 5" }],
  [
    "path",
    {
      d: "m17.5 10.5 3.148 3.148a1.205 1.205 0 0 1 0 1.704l-2.296 2.296a1.205 1.205 0 0 1-1.704 0L13.5 14.5"
    }
  ],
  ["path", { d: "M9 21a6 6 0 0 0-6-6" }],
  [
    "path",
    {
      d: "M9.352 10.648a1.205 1.205 0 0 0 0 1.704l2.296 2.296a1.205 1.205 0 0 0 1.704 0l4.296-4.296a1.205 1.205 0 0 0 0-1.704l-2.296-2.296a1.205 1.205 0 0 0-1.704 0z"
    }
  ]
],
  'scan': [
  ["path", { d: "M3 7V5a2 2 0 0 1 2-2h2" }],
  ["path", { d: "M17 3h2a2 2 0 0 1 2 2v2" }],
  ["path", { d: "M21 17v2a2 2 0 0 1-2 2h-2" }],
  ["path", { d: "M7 21H5a2 2 0 0 1-2-2v-2" }]
],
  'send-horizontal': [
  [
    "path",
    {
      d: "M3.714 3.048a.498.498 0 0 0-.683.627l2.843 7.627a2 2 0 0 1 0 1.396l-2.842 7.627a.498.498 0 0 0 .682.627l18-8.5a.5.5 0 0 0 0-.904z"
    }
  ],
  ["path", { d: "M6 12h16" }]
],
  'sparkles': [
  [
    "path",
    {
      d: "M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"
    }
  ],
  ["path", { d: "M20 2v4" }],
  ["path", { d: "M22 4h-4" }],
  ["circle", { cx: "4", cy: "20", r: "2" }]
],
  'square': [["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }]],
  'workflow': [
  ["rect", { width: "8", height: "8", x: "3", y: "3", rx: "2" }],
  ["path", { d: "M7 11v4a2 2 0 0 0 2 2h4" }],
  ["rect", { width: "8", height: "8", x: "13", y: "13", rx: "2" }]
],
  'x': [
  ["path", { d: "M18 6 6 18" }],
  ["path", { d: "m6 6 12 12" }]
],
  };

  function createSvgElement(tag, attributes) {
    const element = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes || {}).forEach(([name, value]) => {
      element.setAttribute(name, String(value));
    });
    return element;
  }

  function createIcons(options = {}) {
    const nameAttribute = options.nameAttr || "data-lucide";
    const root = options.root || document;
    const commonAttributes = {
      xmlns: SVG_NS,
      width: 24,
      height: 24,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      ...(options.attrs || {}),
    };

    Array.from(root.querySelectorAll(`[${nameAttribute}]`)).forEach((placeholder) => {
      const iconName = placeholder.getAttribute(nameAttribute);
      const nodes = iconNodes[iconName];
      if (!nodes) {
        console.warn(`Unknown Lucide icon: ${iconName}`);
        return;
      }

      const svg = createSvgElement("svg", commonAttributes);
      Array.from(placeholder.attributes).forEach(({ name, value }) => {
        if (name !== "class") svg.setAttribute(name, value);
      });

      const originalClasses = (placeholder.getAttribute("class") || "")
        .split(/\s+/)
        .filter((className) => className && className !== "lucide" && !className.startsWith("lucide-"));
      svg.setAttribute("class", [...originalClasses, "lucide", `lucide-${iconName}`].join(" "));

      nodes.forEach(([tag, attributes]) => {
        svg.appendChild(createSvgElement(tag, attributes));
      });
      placeholder.replaceWith(svg);
    });
  }

  global.lucide = Object.freeze({ createIcons, icons: iconNodes });
})(window);
