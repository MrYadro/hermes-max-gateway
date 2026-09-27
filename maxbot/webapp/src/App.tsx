import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Check,
  Compass,
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

const ICON_SIZE = 20;

const TYPE_META: Record<string, { Icon: typeof User; label: string }> = {
  dm: { Icon: User, label: "Личный чат" },
  group: { Icon: Users, label: "Группа" },
  channel: { Icon: Megaphone, label: "Канал" },
  unknown: { Icon: MessageCircle, label: "Чат" },
};

const GRADIENTS = ["red", "orange", "green", "blue", "purple"] as const;
type Gradient = (typeof GRADIENTS)[number];

/** Детерминированный градиент по имени: у каждого профиля свой стабильный цвет */
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

type View = "chat" | "chats" | "profiles";

export default function App() {
  const here = currentChat();
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>(here ? "chat" : "chats");
  const [focus, setFocus] = useState<string | null>(here?.id ?? null);
  const [busy, setBusy] = useState(false);

  const openChat = (chatId: string) => {
    setFocus(chatId);
    setView("chat");
  };

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
        <CellAction mode="secondary" onClick={() => { setError(""); load(); }}>
          Повторить
        </CellAction>
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

  /** Экран текущего чата: чистый выбор профиля — аватар, имя, серое описание */
  const ProfilePicker = ({ chatId }: { chatId: string }) => {
    const current = profileOf(chatId);
    return (
      <CellList mode="island">
        {state.profiles.map((p) => (
          <CellSimple
            key={p.name}
            before={<ProfileAvatar name={p.name} size={44} />}
            title={p.name}
            subtitle={p.description || undefined}
            after={p.name === current ? <Check size={20} strokeWidth={2} /> : undefined}
            disabled={busy}
            onClick={() => p.name !== current && pick(chatId, p.name)}
          />
        ))}
      </CellList>
    );
  };

  const NavRow = ({ chatId }: { chatId: string }) => (
    <CellList mode="island">
      <CellAction
        mode="secondary"
        height="compact"
        before={<ArrowLeft size={ICON_SIZE} strokeWidth={2} />}
        onClick={() => openChat(chatId)}
      >
        Этот чат
      </CellAction>
    </CellList>
  );

  return (
    <Panel mode="secondary">
      <Container>
        {view === "chat" && focus ? (
          <>
            {here && focus !== here.id && <NavRow chatId={here.id} />}
            <ProfilePicker chatId={focus} />
            <CellList mode="island">
              <CellAction
                mode="secondary"
                height="compact"
                before={<Compass size={ICON_SIZE} strokeWidth={2} />}
                onClick={() => setView("chats")}
              >
                Все чаты
              </CellAction>
            </CellList>
          </>
        ) : view === "chats" ? (
          <>
            {here && <NavRow chatId={here.id} />}
            {state.chats.length === 0 ? (
              <Typography.Body>Чатов пока нет — напишите боту.</Typography.Body>
            ) : (
              <CellList mode="island">
                {state.chats.map((c) => {
                  const meta = TYPE_META[c.chat_type] ?? TYPE_META.unknown;
                  return (
                    <CellSimple
                      key={c.chat_id}
                      height="compact"
                      showChevron
                      before={<ProfileAvatar name={c.profile} size={36} />}
                      title={c.chat_id}
                      subtitle={meta.label}
                      after={<Typography.Body variant="small">{c.profile}</Typography.Body>}
                      onClick={() => openChat(c.chat_id)}
                    />
                  );
                })}
              </CellList>
            )}
            <CellList mode="island">
              <CellAction
                mode="secondary"
                height="compact"
                before={<Compass size={ICON_SIZE} strokeWidth={2} />}
                onClick={() => setView("profiles")}
              >
                Все профили
              </CellAction>
            </CellList>
          </>
        ) : (
          <>
            {here && <NavRow chatId={here.id} />}
            <CellList mode="island" header={<CellHeader>Профили</CellHeader>}>
              {state.profiles.map((p) => (
                <CellSimple
                  key={p.name}
                  before={<ProfileAvatar name={p.name} size={44} />}
                  title={p.name}
                  subtitle={p.description || undefined}
                />
              ))}
            </CellList>
            <CellList mode="island">
              <CellAction
                mode="secondary"
                height="compact"
                before={<ArrowLeft size={ICON_SIZE} strokeWidth={2} />}
                onClick={() => setView("chats")}
              >
                Все чаты
              </CellAction>
            </CellList>
          </>
        )}
      </Container>
    </Panel>
  );
}
