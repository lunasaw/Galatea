import iconUrl from "../public/favicon.svg";

export const BRAND_NAME = "open-codex";

export function BrandIcon() {
  return <img className="open-codex-icon" src={iconUrl} width={64} height={64} alt="" aria-hidden="true" />;
}
