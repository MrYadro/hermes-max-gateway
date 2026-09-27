import { useEffect, useState } from "react";
import { Button, CellList, CellSimple, Panel, Spinner, Typography } from "@maxhub/max-ui";
import { api, StateResponse } from "./api";

const TYPE_ICONS: Record<string, string> = { dm: "👤", group: "👥", channel: "📢", unknown: "💬" };

export default function App() {
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"chats" | "profiles">("chats");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.state().then(setState).catch((e) => setError(String(e.message ?? e)));

  useEffect(() => { load(); }, []);

  const pick = async (chatId: string, profile: string) => {
    setBusy(true);
    try {
      const r = await api.set(chatId, profile);
      setState(r.state);
      setOpen(null);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <Panel mode="secondary">
        <Typography.Body>{error}</Typography.Body>
        <Button
          onClick={() => { setError(""); load(); }}
        >
          Повторить
        </Button>
      </Panel>
    );
  }
  if (!state) {
    return (
      <Panel mode="secondary" centeredX centeredY>
        <Spinner />
      </Panel>
    );
  }

  return (
    <Panel mode="secondary">
      <Typography.Title>Assistants</Typography.Title>
      <CellList>
        <CellSimple
          title="Чаты"
          after={tab === "chats" ? "•" : undefined}
          onClick={() => setTab("chats")}
        />
        <CellSimple
          title="Профили"
          after={tab === "profiles" ? "•" : undefined}
          onClick={() => setTab("profiles")}
        />
      </CellList>
      {tab === "chats" ? (
        state.chats.length === 0 ? (
          <Typography.Body>Чатов пока нет — напишите боту.</Typography.Body>
        ) : (
          <CellList header="Чаты">
            {state.chats.map((c) => (
              <div key={c.chat_id}>
                <CellSimple
                  title={`${TYPE_ICONS[c.chat_type] ?? "💬"} ${c.chat_id}`}
                  after={c.profile}
                  showChevron
                  separator={open !== c.chat_id}
                  onClick={() => setOpen(open === c.chat_id ? null : c.chat_id)}
                />
                {open === c.chat_id && (
                  <CellList mode="island" header="Профиль ассистента">
                    {state.profiles.map((p) => (
                      <Button
                        key={p.name}
                        stretched
                        disabled={busy}
                        variant={p.name === c.profile ? "primary" : "secondary"}
                        onClick={() => pick(c.chat_id, p.name)}
                      >
                        {p.name === c.profile ? `✓ ${p.name}` : p.name}
                      </Button>
                    ))}
                  </CellList>
                )}
              </div>
            ))}
          </CellList>
        )
      ) : (
        <CellList header="Профили">
          {state.profiles.map((p) => (
            <CellSimple key={p.name} title={p.name} subtitle={p.description || "—"} />
          ))}
        </CellList>
      )}
    </Panel>
  );
}
