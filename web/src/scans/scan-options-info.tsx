import type { Scan } from "../api/api-scan-types";
import { scanLabel, scanOriginLabel } from "../shared/display";
import { InfoButton, type InfoButtonHost } from "../ui/InfoButton";
import { scanOptionsSummary } from "./scan-components";

export type ScanOptionsHost = InfoButtonHost;

export function ScanOptionsInfo({ scan, host }: { scan: Scan; host: ScanOptionsHost }) {
  return <InfoButton label={`Show options for ${scanLabel(scan)}`} host={host}>
    <strong>Scan options</strong><span>{scanOptionsSummary(scan)} · {scanOriginLabel(scan)}</span>
  </InfoButton>;
}
