import type {} from "react-syntax-highlighter";
import Light from "react-syntax-highlighter/dist/esm/light";
import javascript from "react-syntax-highlighter/dist/esm/languages/hljs/javascript";
import typescript from "react-syntax-highlighter/dist/esm/languages/hljs/typescript";
import python from "react-syntax-highlighter/dist/esm/languages/hljs/python";
import bash from "react-syntax-highlighter/dist/esm/languages/hljs/bash";
import json from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import sql from "react-syntax-highlighter/dist/esm/languages/hljs/sql";
import yaml from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import xml from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import css from "react-syntax-highlighter/dist/esm/languages/hljs/css";

for (const [name, language] of Object.entries({
  javascript,
  typescript,
  python,
  bash,
  json,
  sql,
  yaml,
  xml,
  css,
}))
  Light.registerLanguage(name, language);

export { Light };
