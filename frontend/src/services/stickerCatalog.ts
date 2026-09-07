/**
 * Hauseigener 20-teiliger Singra-Sticker-Katalog und vollständige kategorisierte Emoji-Palette.
 */

export interface InHouseSticker {
  id: string
  label: string
  svg: string
}

export const IN_HOUSE_STICKERS: InHouseSticker[] = [
  {
    id: 'singra-logo',
    label: 'Singra Shield',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="sg1" x1="10" y1="10" x2="90" y2="90" gradientUnits="userSpaceOnUse">
          <stop stop-color="#38BDF8"/>
          <stop offset="0.5" stop-color="#0284C7"/>
          <stop offset="1" stop-color="#0369A1"/>
        </linearGradient>
        <linearGradient id="sg2" x1="20" y1="20" x2="80" y2="80" gradientUnits="userSpaceOnUse">
          <stop stop-color="#34D399"/>
          <stop offset="1" stop-color="#059669"/>
        </linearGradient>
      </defs>
      <polygon points="50,5 90,25 90,75 50,95 10,75 10,25" fill="url(#sg1)" stroke="#E0F2FE" stroke-width="3" stroke-linejoin="round"/>
      <path d="M50 20 L75 35 V60 C75 75 50 85 50 85 C50 85 25 75 25 60 V35 Z" fill="url(#sg2)" stroke="#FFFFFF" stroke-width="2"/>
      <path d="M40 50 L47 57 L62 42" stroke="#FFFFFF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
  },
  {
    id: 'vault-e2ee',
    label: 'Vault E2EE',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="vt1" x1="0" y1="0" x2="100" y2="100" gradientUnits="userSpaceOnUse">
          <stop stop-color="#8B5CF6"/>
          <stop offset="1" stop-color="#6D28D9"/>
        </linearGradient>
      </defs>
      <rect x="15" y="38" width="70" height="52" rx="14" fill="url(#vt1)" stroke="#DDD6FE" stroke-width="3"/>
      <path d="M32 38 V26 C32 16 40 8 50 8 C60 8 68 16 68 26 V38" stroke="#F59E0B" stroke-width="8" stroke-linecap="round"/>
      <circle cx="50" cy="60" r="7" fill="#FDE68A"/>
      <path d="M50 67 V76" stroke="#FDE68A" stroke-width="5" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'server-blade',
    label: 'Server Rack',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="srv1" x1="0" y1="0" x2="0" y2="100" gradientUnits="userSpaceOnUse">
          <stop stop-color="#1E293B"/>
          <stop offset="1" stop-color="#0F172A"/>
        </linearGradient>
      </defs>
      <rect x="12" y="10" width="76" height="80" rx="10" fill="url(#srv1)" stroke="#38BDF8" stroke-width="3"/>
      <rect x="20" y="20" width="60" height="14" rx="4" fill="#334155"/>
      <circle cx="28" cy="27" r="3" fill="#10B981"/>
      <circle cx="36" cy="27" r="3" fill="#38BDF8"/>
      <line x1="50" y1="27" x2="72" y2="27" stroke="#64748B" stroke-width="3" stroke-linecap="round"/>
      <rect x="20" y="43" width="60" height="14" rx="4" fill="#334155"/>
      <circle cx="28" cy="50" r="3" fill="#10B981"/>
      <circle cx="36" cy="50" r="3" fill="#F59E0B"/>
      <line x1="50" y1="50" x2="72" y2="50" stroke="#64748B" stroke-width="3" stroke-linecap="round"/>
      <rect x="20" y="66" width="60" height="14" rx="4" fill="#334155"/>
      <circle cx="28" cy="73" r="3" fill="#10B981"/>
      <circle cx="36" cy="73" r="3" fill="#10B981"/>
      <line x1="50" y1="73" x2="72" y2="73" stroke="#64748B" stroke-width="3" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'master-key',
    label: 'Master Key',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="key1" x1="10" y1="10" x2="90" y2="90" gradientUnits="userSpaceOnUse">
          <stop stop-color="#FDE047"/>
          <stop offset="0.5" stop-color="#EAB308"/>
          <stop offset="1" stop-color="#CA8A04"/>
        </linearGradient>
      </defs>
      <circle cx="34" cy="40" r="22" stroke="url(#key1)" stroke-width="9"/>
      <path d="M50 48 L88 86" stroke="url(#key1)" stroke-width="9" stroke-linecap="round"/>
      <path d="M74 72 L83 63" stroke="url(#key1)" stroke-width="7" stroke-linecap="round"/>
      <path d="M82 80 L90 72" stroke="url(#key1)" stroke-width="7" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'terminal-cli',
    label: 'Terminal Prompt',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="16" width="80" height="68" rx="8" fill="#090D16" stroke="#22C55E" stroke-width="2.5"/>
      <rect x="10" y="16" width="80" height="14" rx="8" fill="#1E293B"/>
      <circle cx="20" cy="23" r="3" fill="#EF4444"/>
      <circle cx="28" cy="23" r="3" fill="#EAB308"/>
      <circle cx="36" cy="23" r="3" fill="#22C55E"/>
      <path d="M22 45 L34 53 L22 61" stroke="#22C55E" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <line x1="42" y1="61" x2="58" y2="61" stroke="#22C55E" stroke-width="4" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'turbo-lightning',
    label: 'Turbo Flash',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="lg1" x1="20" y1="0" x2="80" y2="100" gradientUnits="userSpaceOnUse">
          <stop stop-color="#FDE047"/>
          <stop offset="1" stop-color="#EA580C"/>
        </linearGradient>
      </defs>
      <polygon points="56,4 18,54 48,54 38,96 82,44 52,44" fill="url(#lg1)" stroke="#FFFBEB" stroke-width="2.5" stroke-linejoin="round"/>
    </svg>`,
  },
  {
    id: 'flame-fire',
    label: 'Feuer',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="flm1" x1="50" y1="10" x2="50" y2="90" gradientUnits="userSpaceOnUse">
          <stop stop-color="#F97316"/>
          <stop offset="0.7" stop-color="#DC2626"/>
          <stop offset="1" stop-color="#7F1D1D"/>
        </linearGradient>
        <linearGradient id="flm2" x1="50" y1="35" x2="50" y2="85" gradientUnits="userSpaceOnUse">
          <stop stop-color="#FDE047"/>
          <stop offset="1" stop-color="#EA580C"/>
        </linearGradient>
      </defs>
      <path d="M50 8 C50 8 78 36 78 62 C78 78 65 92 50 92 C35 92 22 78 22 62 C22 42 36 28 42 20 C42 30 46 38 52 38 C56 38 60 32 60 26 C60 18 50 8 50 8 Z" fill="url(#flm1)"/>
      <path d="M50 40 C50 40 64 54 64 68 C64 78 58 84 50 84 C42 84 36 78 36 68 C36 56 46 48 50 40 Z" fill="url(#flm2)"/>
    </svg>`,
  },
  {
    id: 'rocket-launch',
    label: 'Rocket Launch',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M72 12 C72 12 50 18 36 34 C28 44 26 56 26 56 L44 74 C44 74 56 72 66 64 C82 50 88 28 88 28 C88 28 78 22 72 12 Z" fill="#E2E8F0" stroke="#0284C7" stroke-width="3"/>
      <circle cx="58" cy="42" r="8" fill="#38BDF8" stroke="#0369A1" stroke-width="2"/>
      <polygon points="26,56 12,62 18,74 32,70" fill="#EF4444"/>
      <polygon points="44,74 38,88 50,82 46,68" fill="#EF4444"/>
      <polygon points="22,76 10,92 24,84" fill="#F97316"/>
    </svg>`,
  },
  {
    id: 'coffee-mug',
    label: 'Dev Coffee',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="cm1" x1="20" y1="35" x2="80" y2="85" gradientUnits="userSpaceOnUse">
          <stop stop-color="#D97706"/>
          <stop offset="1" stop-color="#92400E"/>
        </linearGradient>
      </defs>
      <rect x="22" y="36" width="46" height="50" rx="10" fill="url(#cm1)" stroke="#FDE68A" stroke-width="2.5"/>
      <path d="M68 46 C78 46 84 52 84 60 C84 68 78 74 68 74" stroke="#FDE68A" stroke-width="5" stroke-linecap="round"/>
      <path d="M34 26 C32 20 38 14 36 8" stroke="#F59E0B" stroke-width="3" stroke-linecap="round"/>
      <path d="M45 28 C43 22 49 16 47 10" stroke="#F59E0B" stroke-width="3" stroke-linecap="round"/>
      <path d="M56 26 C54 20 60 14 58 8" stroke="#F59E0B" stroke-width="3" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'ai-robot',
    label: 'KI Bot Luna',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="20" y="24" width="60" height="52" rx="16" fill="#1E293B" stroke="#38BDF8" stroke-width="3"/>
      <line x1="50" y1="24" x2="50" y2="10" stroke="#38BDF8" stroke-width="4" stroke-linecap="round"/>
      <circle cx="50" cy="8" r="5" fill="#06B6D4"/>
      <circle cx="38" cy="46" r="7" fill="#38BDF8"/>
      <circle cx="62" cy="46" r="7" fill="#38BDF8"/>
      <path d="M38 62 Q50 72 62 62" stroke="#38BDF8" stroke-width="3.5" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'gaming-pad',
    label: 'Gaming Pad',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M22 34 C12 34 6 52 14 74 C18 84 28 84 34 76 L44 64 H56 L66 76 C72 84 82 84 86 74 C94 52 88 34 78 34 Z" fill="#334155" stroke="#A855F7" stroke-width="3"/>
      <circle cx="72" cy="48" r="3.5" fill="#EF4444"/>
      <circle cx="66" cy="54" r="3.5" fill="#3B82F6"/>
      <circle cx="78" cy="54" r="3.5" fill="#EAB308"/>
      <circle cx="72" cy="60" r="3.5" fill="#22C55E"/>
      <path d="M28 46 V58 M22 52 H34" stroke="#CBD5E1" stroke-width="3.5" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'singra-gem',
    label: 'Diamond Gem',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <polygon points="30,16 70,16 92,42 50,88 8,42" fill="#0EA5E9" stroke="#E0F2FE" stroke-width="3"/>
      <polygon points="30,16 50,42 70,16" fill="#38BDF8"/>
      <polygon points="8,42 50,42 30,16" fill="#0284C7"/>
      <polygon points="92,42 50,42 70,16" fill="#0284C7"/>
      <polygon points="8,42 50,88 50,42" fill="#0369A1"/>
      <polygon points="92,42 50,88 50,42" fill="#075985"/>
    </svg>`,
  },
  {
    id: 'party-popper',
    label: 'Feier Party',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <polygon points="12,88 36,36 64,64" fill="#F59E0B" stroke="#FEF3C7" stroke-width="2.5"/>
      <circle cx="60" cy="30" r="4" fill="#EC4899"/>
      <circle cx="78" cy="45" r="4" fill="#3B82F6"/>
      <circle cx="48" cy="18" r="3.5" fill="#10B981"/>
      <circle cx="82" cy="22" r="5" fill="#8B5CF6"/>
      <path d="M42 28 Q50 20 62 26" stroke="#EF4444" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M68 40 Q74 34 84 42" stroke="#F59E0B" stroke-width="2.5" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'bug-squash',
    label: 'Bug Hunter',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <ellipse cx="50" cy="55" rx="20" ry="26" fill="#10B981" stroke="#ECFDF5" stroke-width="3"/>
      <circle cx="50" cy="26" r="12" fill="#047857"/>
      <line x1="42" y1="20" x2="32" y2="10" stroke="#10B981" stroke-width="3" stroke-linecap="round"/>
      <line x1="58" y1="20" x2="68" y2="10" stroke="#10B981" stroke-width="3" stroke-linecap="round"/>
      <line x1="30" y1="46" x2="14" y2="40" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="30" y1="58" x2="12" y2="60" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="30" y1="70" x2="16" y2="78" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="70" y1="46" x2="86" y2="40" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="70" y1="58" x2="88" y2="60" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="70" y1="70" x2="84" y2="78" stroke="#10B981" stroke-width="3.5" stroke-linecap="round"/>
    </svg>`,
  },
  {
    id: 'check-verified',
    label: 'Geprüft Check',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="42" fill="#10B981" stroke="#A7F3D0" stroke-width="4"/>
      <path d="M30 50 L44 64 L72 34" stroke="#FFFFFF" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
  },
  {
    id: 'heart-pulse',
    label: 'Cyber Heart',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="hp1" x1="10" y1="10" x2="90" y2="90" gradientUnits="userSpaceOnUse">
          <stop stop-color="#F43F5E"/>
          <stop offset="1" stop-color="#BE123C"/>
        </linearGradient>
      </defs>
      <path d="M50 86 C50 86 16 62 16 36 C16 22 28 12 42 16 C48 18 50 24 50 24 C50 24 52 18 58 16 C72 12 84 22 84 36 C84 62 50 86 50 86 Z" fill="url(#hp1)" stroke="#FECDD3" stroke-width="2.5"/>
      <path d="M26 48 H40 L46 36 L54 60 L60 48 H74" stroke="#FFFFFF" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
  },
  {
    id: 'star-sparkle',
    label: 'Stern Gold',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <polygon points="50,6 63,35 94,38 71,59 78,90 50,74 22,90 29,59 6,38 37,35" fill="#FBBF24" stroke="#FDE68A" stroke-width="3" stroke-linejoin="round"/>
      <circle cx="50" cy="50" r="10" fill="#F59E0B"/>
    </svg>`,
  },
  {
    id: 'thumbs-up',
    label: 'Daumen Hoch',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="44" width="16" height="38" rx="4" fill="#0284C7" stroke="#BAE6FD" stroke-width="2"/>
      <path d="M30 52 H46 C52 52 56 46 56 40 C56 30 50 24 50 14 C54 12 60 14 62 18 C64 24 62 32 60 38 H80 C86 38 88 44 86 50 L80 74 C78 78 74 82 68 82 H30" fill="#0EA5E9" stroke="#BAE6FD" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
  },
  {
    id: 'skull-danger',
    label: 'Cyber Skull',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M24 44 C24 24 34 14 50 14 C66 14 76 24 76 44 C76 56 70 64 64 68 V80 H36 V68 C30 64 24 56 24 44 Z" fill="#E2E8F0" stroke="#475569" stroke-width="3"/>
      <circle cx="38" cy="46" r="7" fill="#0F172A"/>
      <circle cx="62" cy="46" r="7" fill="#0F172A"/>
      <polygon points="50,56 46,64 54,64" fill="#0F172A"/>
      <line x1="43" y1="74" x2="43" y2="80" stroke="#0F172A" stroke-width="2.5"/>
      <line x1="50" y1="74" x2="50" y2="80" stroke="#0F172A" stroke-width="2.5"/>
      <line x1="57" y1="74" x2="57" y2="80" stroke="#0F172A" stroke-width="2.5"/>
    </svg>`,
  },
  {
    id: 'lock-open',
    label: 'Unlocked Key',
    svg: `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="18" y="44" width="64" height="46" rx="12" fill="#10B981" stroke="#A7F3D0" stroke-width="3"/>
      <path d="M34 44 V26 C34 16 42 8 52 8 C62 8 70 16 70 26" stroke="#F59E0B" stroke-width="8" stroke-linecap="round"/>
      <circle cx="50" cy="64" r="6" fill="#ECFDF5"/>
      <path d="M50 70 V77" stroke="#ECFDF5" stroke-width="4" stroke-linecap="round"/>
    </svg>`,
  },
]

export interface EmojiCategory {
  category: string
  emojis: string[]
}

export const CATEGORIZED_EMOJIS: EmojiCategory[] = [
  {
    category: 'Smileys & Gefühle',
    emojis: [
      '😀', '😃', '😄', '😁', '😆', '😅', '😂', '🤣',
      '😊', '😇', '🙂', '🙃', '😉', '😌', '😍', '🥰',
      '😘', '😋', '😛', '😜', '🤪', '😎', '🤩', '🥳',
      '😏', '😒', '😞', '😔', '😟', '😕', '🙁', '😣',
      '😖', '😫', '😩', '🥺', '😢', '😭', '😤', '😠',
      '😡', '🤬', '🤯', '😳', '🥵', '🥶', '😱', '😨',
      '😰', '😥', '😓', '🤗', '🤔', '🤭', '🤫', '🤥',
      '😶', '😐', '😑', '😬', '🙄', '😯', '😦', '😧',
      '😮', '😲', '🥱', '😴', '🤤', '😪', '😵', '🤐',
    ],
  },
  {
    category: 'Gesten & Menschen',
    emojis: [
      '👍', '👎', '👊', '✊', '🤛', '🤜', '👏', '🙌',
      '👐', '🤲', '🤝', '🙏', '✌️', '🤞', '🤟', '🤘',
      '🤙', '👈', '👉', '👆', '🖕', '👇', '☝️', '👋',
      '🤚', '🖐️', '✋', '🖖', '👌', '🤌', '🤏', '✍️',
      '💅', '🤳', '💪', '🦾', '🧠', '🫀', '👀', '👁️',
      '🧑‍💻', '👨‍💻', '👩‍💻', '🕵️', '🧙', '🦸', '🦹', '🤖',
    ],
  },
  {
    category: 'Sicherheit & Tech',
    emojis: [
      '🛡️', '🔒', '🔓', '🔏', '🔐', '🔑', '🗝️', '⚙️',
      '⚡', '💡', '🔥', '✨', '🌟', '💥', '💯', '💢',
      '🖥️', '💻', '⌨️', '🖱️', '📱', '📡', '🛰️', '🕹️',
      '🚀', '🛸', '🎯', '💾', '💿', '📀', '🔌', '🔋',
      '💬', '💭', '🗯️', '📢', '🔔', '🔕', '🎵', '🎶',
    ],
  },
  {
    category: 'Aktivität & Essen',
    emojis: [
      '🎮', '☕', '🍵', '🍕', '🍔', '🍟', '🌭', '🍿',
      '🍺', '🍻', '🥂', '🍷', '🏆', '🥇', '🥈', '🥉',
      '⚽', '🏀', '🏈', '⚾', '🎾', '🏐', '🎳', '🎲',
      '🎉', '🎊', '🎈', '🎁', '🎪', '🎨', '🏖️', '🏝️',
    ],
  },
]
