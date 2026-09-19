import { CommandLog } from "../components/CommandLog";
import { Page, Panel } from "../components/ui";

export function LogScreen() {
  return (
    <Page
      title="Command log"
      lead="Every command this window has run, as you would type it. Each one also writes to the case's own audit chain; this log is only a convenience and is not evidence."
    >
      <Panel>
        <CommandLog />
      </Panel>
    </Page>
  );
}
