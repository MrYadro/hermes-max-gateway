import { useEffect, useState } from "react";
import {
  CellAction,
  CellHeader,
  CellList,
  CellSimple,
  Container,
  Panel,
  Spinner,
  Typography,
} from "@maxhub/max-ui";
import { api, StateResponse } from "./api";

const TYPE_META: Record<string, { icon: string; label: string }> = {
  dm: { icon: "👤", label: "Личный чат" },
  group: { icon: "👥", label: "Группа" },
  channel: { icon: "📢", label: "Канал" },
  unknown: { icon: "💬", label: "Чат" },
};

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
      <Panel mode="secondary" centeredX centeredY>
        <Typography.Body>{error}</Typography.Body>
        <CellAction onClick={() => { setError(""); load(); }}>Повторить</CellAction>
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
      <Container>
        <CellHeader
          titleStyle="caps"
          fullWidth
          after={
            <Typography.Label>
              {state.profiles.length} ассистент(ов)
            </Typography.Label>
          }
        >
          Assistants
        </CellHeader>

        <CellList mode="island">
          <CellAction
            before={<span>💬</span>}
            showChevron={false}
            mode={tab === "chats" ? "primary" : "secondary"}
            onClick={() => setTab("chats")}
          >
            Чаты
          </CellAction>
          <CellAction
            before={<span>🧭</span>}
            showChevron={false}
            mode={tab === "profiles" ? "primary" : "secondary"}
            onClick={() => setTab("profiles")}
          >
            Профили
          </CellAction>
        </CellList>

        {tab === "chats" ? (
          state.chats.length === 0 ? (
            <Typography.Body>Чатов пока нет — напишите боту.</Typography.Body>
          ) : (
            state.chats.map((c) => {
              const meta = TYPE_META[c.chat_type] ?? TYPE_META.unknown;
              const expanded = open === c.chat_id;
              return (
                <CellList
                  key={c.chat_id}
                  mode="island"
                  header={
                    <CellHeader after={<Typography.Label>{meta.label}</Typography.Label>}>
                      {meta.icon} {c.chat_id}
                    </CellHeader>
                  }
                >
                  <CellSimple
                    title="Ассистент"
                    after={<Typography.Label>{c.profile}</Typography.Label>}
                    showChevron
                    separator={!expanded}
                    onClick={() => setOpen(expanded ? null : c.chat_id)}
                  />
                  {expanded &&
                    state.profiles.map((p) => (
                      <CellAction
                        key={p.name}
                        before={p.name === c.profile ? <span>✓</span> : <span>•</span>}
                        showChevron={false}
                        disabled={busy || p.name === c.profile}
                        onClick={() => pick(c.chat_id, p.name)}
                      >
                        {p.name}
                        {p.description ? ` — ${p.description}` : ""}
                      </CellAction>
                    ))}
                </CellList>
              );
            })
          )
        ) : (
          <CellList
            mode="island"
            header={<CellHeader>Профили</CellHeader>}
          >
            {state.profiles.map((p) => (
              <CellSimple
                key={p.name}
                title={p.name}
                subtitle={p.description || "—"}
                after={p.name === "default" ? <Typography.Label>базовый</Typography.Label> : undefined}
              />
            ))}
          </CellList>
        )}
      </Container>
    </Panel>
  );
}
