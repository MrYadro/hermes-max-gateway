import { useEffect, useState } from "react";
import {
  Avatar,
  CellAction,
  CellHeader,
  CellList,
  CellSimple,
  Container,
  Panel,
  Spinner,
  Typography,
} from "@maxhub/max-ui";
import { api, CurrentChat, currentChat, StateResponse } from "./api";

const TYPE_META: Record<string, { icon: string; label: string }> = {
  dm: { icon: "👤", label: "Личный чат" },
  group: { icon: "👥", label: "Группа" },
  channel: { icon: "📢", label: "Канал" },
  unknown: { icon: "💬", label: "Чат" },
};

const GRADIENTS = ["red", "orange", "green", "blue", "purple"] as const;
type Gradient = (typeof GRADIENTS)[number];

/** Детерминированный градиент по имени: у каждого профиля/чата свой стабильный цвет */
function gradientOf(name: string): Gradient {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return GRADIENTS[h % GRADIENTS.length];
}

function ProfileAvatar({ name, size = 40 }: { name: string; size?: number }) {
  return (
    <Avatar.Container size={size} form="squircle">
      <Avatar.Text gradient={gradientOf(name)}>{name.slice(0, 1).toUpperCase()}</Avatar.Text>
    </Avatar.Container>
  );
}

function ChatAvatar({ chatId, type, size = 40 }: { chatId: string; type: string; size?: number }) {
  const icon = (TYPE_META[type] ?? TYPE_META.unknown).icon;
  return (
    <Avatar.Container size={size} form="squircle">
      <Avatar.Text gradient={gradientOf(chatId)}>{icon}</Avatar.Text>
    </Avatar.Container>
  );
}

type View = "current" | "chats" | "profiles";

export default function App() {
  const here = currentChat();
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>(here ? "current" : "chats");
  const [busy, setBusy] = useState(false);

  const load = () => api.state().then(setState).catch((e) => setError(String(e.message ?? e)));

  useEffect(() => { load(); }, []);

  const pick = async (chatId: string, profile: string) => {
    setBusy(true);
    try {
      const r = await api.set(chatId, profile);
      setState(r.state);
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

  const profileOf = (chatId: string) =>
    state.chats.find((c) => c.chat_id === chatId)?.profile ?? "default";
  const typeOf = (chatId: string) =>
    state.chats.find((c) => c.chat_id === chatId)?.chat_type ?? here?.type ?? "unknown";

  const ProfilePicker = ({ chatId, type }: { chatId: string; type: string }) => {
    const current = profileOf(chatId);
    return (
      <>
        <CellSimple
          before={<ChatAvatar chatId={chatId} type={type} />}
          title="Ассистент"
          after={
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <Typography.Label>{current}</Typography.Label>
              <ProfileAvatar name={current} size={24} />
            </span>
          }
        />
        {state!.profiles.map((p) => (
          <CellAction
            key={p.name}
            before={<ProfileAvatar name={p.name} size={28} />}
            showChevron={false}
            disabled={busy || p.name === current}
            onClick={() => pick(chatId, p.name)}
          >
            {p.name}
            {p.description ? ` — ${p.description}` : ""}
          </CellAction>
        ))}
      </>
    );
  };

  const ChatIsland = ({ chatId, type }: { chatId: string; type: string }) => {
    const meta = TYPE_META[type] ?? TYPE_META.unknown;
    return (
      <CellList
        mode="island"
        header={
          <CellHeader after={<Typography.Label>{meta.label}</Typography.Label>}>
            {meta.icon} {chatId}
          </CellHeader>
        }
      >
        <ProfilePicker chatId={chatId} type={type} />
      </CellList>
    );
  };

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

        {view === "current" && here ? (
          <>
            <ChatIsland chatId={here.id} type={typeOf(here.id)} />
            <CellList mode="island">
              <CellAction before={<span>🗂</span>} onClick={() => setView("chats")}>
                Все чаты
              </CellAction>
              <CellAction before={<span>🧭</span>} onClick={() => setView("profiles")}>
                Все профили
              </CellAction>
            </CellList>
          </>
        ) : view === "chats" ? (
          <>
            {here && (
              <CellList mode="island">
                <CellAction before={<span>⬅️</span>} onClick={() => setView("current")}>
                  Этот чат ({here.id})
                </CellAction>
              </CellList>
            )}
            {state.chats.length === 0 ? (
              <Typography.Body>Чатов пока нет — напишите боту.</Typography.Body>
            ) : (
              state.chats.map((c) => (
                <ChatIsland key={c.chat_id} chatId={c.chat_id} type={c.chat_type} />
              ))
            )}
          </>
        ) : (
          <>
            {here && (
              <CellList mode="island">
                <CellAction before={<span>⬅️</span>} onClick={() => setView("current")}>
                  Этот чат ({here.id})
                </CellAction>
              </CellList>
            )}
            <CellList mode="island" header={<CellHeader>Профили</CellHeader>}>
              {state.profiles.map((p) => (
                <CellSimple
                  key={p.name}
                  before={<ProfileAvatar name={p.name} size={44} />}
                  title={p.name}
                  subtitle={p.description || "—"}
                  after={p.name === "default" ? <Typography.Label>базовый</Typography.Label> : undefined}
                />
              ))}
            </CellList>
          </>
        )}
      </Container>
    </Panel>
  );
}
