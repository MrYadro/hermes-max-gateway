import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Compass,
  LayoutGrid,
  Megaphone,
  MessageCircle,
  User,
  Users,
} from "lucide-react";
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

type ChatType = "dm" | "group" | "channel" | "unknown";

const ICON_SIZE = 20;

const TYPE_META: Record<string, { Icon: typeof User; label: string }> = {
  dm: { Icon: User, label: "Личный чат" },
  group: { Icon: Users, label: "Группа" },
  channel: { Icon: Megaphone, label: "Канал" },
  unknown: { Icon: MessageCircle, label: "Чат" },
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

  const ProfilePicker = ({ chatId }: { chatId: string }) => {
    const current = profileOf(chatId);
    return (
      <>
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
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <meta.Icon size={ICON_SIZE} strokeWidth={2} />
              {chatId}
            </span>
          </CellHeader>
        }
      >
        <ProfilePicker chatId={chatId} />
      </CellList>
    );
  };

  return (
    <Panel mode="secondary">
      <Container>

        {view === "current" && here ? (
          <>
            <ChatIsland chatId={here.id} type={typeOf(here.id)} />
            <CellList mode="island">
              <CellAction before={<LayoutGrid size={ICON_SIZE} strokeWidth={2} />} onClick={() => setView("chats")}>
                Все чаты
              </CellAction>
              <CellAction before={<Compass size={ICON_SIZE} strokeWidth={2} />} onClick={() => setView("profiles")}>
                Все профили
              </CellAction>
            </CellList>
          </>
        ) : view === "chats" ? (
          <>
            {here && (
              <CellList mode="island">
                <CellAction before={<ArrowLeft size={ICON_SIZE} strokeWidth={2} />} onClick={() => setView("current")}>
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
                <CellAction before={<ArrowLeft size={ICON_SIZE} strokeWidth={2} />} onClick={() => setView("current")}>
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
