import { useRef, useState } from "react";
import type { MasterTarget } from "../api/api-community-master-types";
import { useCan } from "../auth/auth-gate";
import { ModalDialog } from "../ui/ModalDialog";
import { CommunityMasterPanel } from "./community-master-panel";
import { MasterFindResults } from "./master-find-results";

/**
 * The item workflow's Community tab: Find Filters searches the community service for master filter
 * sets of this version, and the account's linked sets are reviewed here. Accounts without
 * `community.search` see why the tab is empty instead of the controls.
 */
export function CommunityTab({ target }: { target: MasterTarget }) {
  const maySearch = useCan("community.search");
  const [finding, setFinding] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <section className="settings-card community-tab" aria-labelledby="community-tab-heading">
    <div className="section-heading">
      <div>
        <h2 id="community-tab-heading">Community filters</h2>
        <p>Filter sets other people shared for this version. Review them here, then include them in a saved review from the Review tab.</p>
      </div>
      {maySearch && <button type="button" className="btn-primary" ref={trigger} aria-haspopup="dialog"
        onClick={() => setFinding(true)}>Find Filters</button>}
    </div>
    {maySearch
      ? <CommunityMasterPanel target={target} emptyText="No community filters for this version yet. Use Find Filters to look for them." />
      : <p className="muted-copy" role="note">Your account can't use community filters. Ask an administrator for community access.</p>}
    {finding && <ModalDialog labelledBy="community-find-heading" className="find-filters-dialog"
      onClose={() => setFinding(false)} returnFocusRef={trigger}>
      <h2 id="community-find-heading">Find Filters</h2>
      <MasterFindResults target={target} findOnOpen />
      <div className="button-row"><button type="button" className="btn-secondary" onClick={() => setFinding(false)}>Close</button></div>
    </ModalDialog>}
  </section>;
}
