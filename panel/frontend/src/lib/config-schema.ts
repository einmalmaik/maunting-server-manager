export type ConfigSchemaText = {
  en: string
  de: string
}

export type ConfigFieldHelp = {
  label: ConfigSchemaText
  description: ConfigSchemaText
  placeholder?: ConfigSchemaText
}

export const CONFIG_GROUP_LABELS: Record<string, ConfigSchemaText> = {
  identity: { en: 'Server Identity', de: 'Server-Identitaet' },
  rules: { en: 'Access and Rules', de: 'Zugang und Regeln' },
  world: { en: 'World and PvE', de: 'Welt und PvE' },
  time: { en: 'Time', de: 'Zeit' },
  progression: { en: 'XP and Progression', de: 'XP und Fortschritt' },
  combat: { en: 'Combat', de: 'Kampf' },
  building: { en: 'Building and Raiding', de: 'Bauen und Raids' },
  performance: { en: 'Performance', de: 'Performance' },
}

export const CONFIG_FIELD_HELP: Record<string, ConfigFieldHelp> = {
  ServerName: {
    label: { en: 'Server Name', de: 'Servername' },
    description: {
      en: 'Name shown in the Conan Exiles server browser.',
      de: 'Name, der im Conan Exiles Serverbrowser angezeigt wird.',
    },
  },
  AdminPassword: {
    label: { en: 'Admin Password', de: 'Admin-Passwort' },
    description: {
      en: 'Password used for in-game server administration. Keep it private.',
      de: 'Passwort fuer die Administration im Spiel. Bitte vertraulich behandeln.',
    },
  },
  ServerPassword: {
    label: { en: 'Join Password', de: 'Beitritts-Passwort' },
    description: {
      en: 'Optional password required before players can join.',
      de: 'Optionales Passwort, das Spieler vor dem Beitritt eingeben muessen.',
    },
  },
  ServerCommunity: {
    label: { en: 'Community Type', de: 'Community-Typ' },
    description: {
      en: 'Server browser community category used by Conan Exiles.',
      de: 'Community-Kategorie, die Conan Exiles im Serverbrowser verwendet.',
    },
  },
  MaxNudity: {
    label: { en: 'Max Nudity', de: 'Maximale Nacktheit' },
    description: {
      en: 'Allowed nudity level. Regional rules may still override this.',
      de: 'Erlaubte Nacktheitsstufe. Regionale Regeln koennen dies weiter begrenzen.',
    },
  },
  PVPEnabled: {
    label: { en: 'PvP Enabled', de: 'PvP aktiviert' },
    description: {
      en: 'Allows player-versus-player combat on the server.',
      de: 'Erlaubt Spieler-gegen-Spieler-Kampf auf dem Server.',
    },
  },
  IsBattlEyeEnabled: {
    label: { en: 'BattlEye', de: 'BattlEye' },
    description: {
      en: 'Enables BattlEye anti-cheat for connecting players.',
      de: 'Aktiviert BattlEye Anti-Cheat fuer verbindende Spieler.',
    },
  },
  ClanMaxSize: {
    label: { en: 'Clan Max Size', de: 'Maximale Clan-Groesse' },
    description: {
      en: 'Maximum number of players allowed in one clan.',
      de: 'Maximale Anzahl Spieler in einem Clan.',
    },
  },
  MaxPlayers: {
    label: { en: 'Max Players', de: 'Maximale Spieler' },
    description: {
      en: 'Maximum connected players. This also appears in Game.ini on some setups.',
      de: 'Maximale gleichzeitige Spieler. Manche Setups fuehren dies auch in Game.ini.',
    },
  },
  LogoutCharactersRemainInTheWorld: {
    label: { en: 'Offline Bodies Stay', de: 'Offline-Koerper bleiben' },
    description: {
      en: 'Keeps logged-out characters physically present in the world.',
      de: 'Laesst ausgeloggte Charaktere physisch in der Welt stehen.',
    },
  },
  AvatarsDisabled: {
    label: { en: 'Disable Avatars', de: 'Avatare deaktivieren' },
    description: {
      en: 'Disables avatar summoning on the server.',
      de: 'Deaktiviert Avatar-Beschwoerungen auf dem Server.',
    },
  },
  EnableSandStorm: {
    label: { en: 'Sandstorm', de: 'Sandsturm' },
    description: {
      en: 'Enables sandstorm events in the world.',
      de: 'Aktiviert Sandsturm-Ereignisse in der Welt.',
    },
  },
  HarvestAmountMultiplier: {
    label: { en: 'Harvest Amount', de: 'Erntemenge' },
    description: {
      en: 'Multiplier for resources gained from harvesting.',
      de: 'Multiplikator fuer Ressourcen aus dem Sammeln.',
    },
  },
  ResourceRespawnSpeedMultiplier: {
    label: { en: 'Resource Respawn', de: 'Ressourcen-Respawn' },
    description: {
      en: 'Controls how quickly resource nodes return.',
      de: 'Steuert, wie schnell Ressourcenpunkte wieder erscheinen.',
    },
  },
  NPCRespawnMultiplier: {
    label: { en: 'NPC Respawn', de: 'NPC-Respawn' },
    description: {
      en: 'Controls how quickly NPCs respawn.',
      de: 'Steuert, wie schnell NPCs wieder erscheinen.',
    },
  },
  DayCycleSpeedScale: {
    label: { en: 'Day Cycle Speed', de: 'Tageszyklus' },
    description: {
      en: 'Overall multiplier for the full day and night cycle.',
      de: 'Gesamtmultiplikator fuer den Tag- und Nachtzyklus.',
    },
  },
  DayTimeSpeedScale: {
    label: { en: 'Daytime Speed', de: 'Taggeschwindigkeit' },
    description: {
      en: 'Multiplier for daytime progression.',
      de: 'Multiplikator fuer den Tagesverlauf.',
    },
  },
  NightTimeSpeedScale: {
    label: { en: 'Night Speed', de: 'Nachtgeschwindigkeit' },
    description: {
      en: 'Multiplier for night progression.',
      de: 'Multiplikator fuer den Nachtverlauf.',
    },
  },
  DawnDuskSpeedScale: {
    label: { en: 'Dawn/Dusk Speed', de: 'Morgen-/Abenddaemmerung' },
    description: {
      en: 'Multiplier for dawn and dusk transitions.',
      de: 'Multiplikator fuer Morgen- und Abenddaemmerung.',
    },
  },
  PlayerXPRateMultiplier: {
    label: { en: 'XP Rate', de: 'XP-Rate' },
    description: {
      en: 'Overall multiplier for player XP gain.',
      de: 'Gesamtmultiplikator fuer Spieler-XP.',
    },
  },
  PlayerXPKillMultiplier: {
    label: { en: 'Kill XP', de: 'Kill-XP' },
    description: {
      en: 'XP multiplier for kills.',
      de: 'XP-Multiplikator fuer Kills.',
    },
  },
  PlayerXPHarvestMultiplier: {
    label: { en: 'Harvest XP', de: 'Sammel-XP' },
    description: {
      en: 'XP multiplier for harvesting.',
      de: 'XP-Multiplikator fuer Sammeln.',
    },
  },
  PlayerXPCraftMultiplier: {
    label: { en: 'Craft XP', de: 'Crafting-XP' },
    description: {
      en: 'XP multiplier for crafting.',
      de: 'XP-Multiplikator fuer Crafting.',
    },
  },
  PlayerXPTimeMultiplier: {
    label: { en: 'Passive XP', de: 'Passive XP' },
    description: {
      en: 'XP multiplier for time-based progression.',
      de: 'XP-Multiplikator fuer zeitbasierten Fortschritt.',
    },
  },
  PlayerDamageMultiplier: {
    label: { en: 'Player Damage', de: 'Spielerschaden' },
    description: {
      en: 'Outgoing damage multiplier for players.',
      de: 'Ausgehender Schadensmultiplikator fuer Spieler.',
    },
  },
  PlayerDamageTakenMultiplier: {
    label: { en: 'Player Damage Taken', de: 'Spieler-Schaden genommen' },
    description: {
      en: 'Incoming damage multiplier for players.',
      de: 'Eingehender Schadensmultiplikator fuer Spieler.',
    },
  },
  NPCDamageMultiplier: {
    label: { en: 'NPC Damage', de: 'NPC-Schaden' },
    description: {
      en: 'Outgoing damage multiplier for NPCs.',
      de: 'Ausgehender Schadensmultiplikator fuer NPCs.',
    },
  },
  NPCDamageTakenMultiplier: {
    label: { en: 'NPC Damage Taken', de: 'NPC-Schaden genommen' },
    description: {
      en: 'Incoming damage multiplier for NPCs.',
      de: 'Eingehender Schadensmultiplikator fuer NPCs.',
    },
  },
  MinionDamageMultiplier: {
    label: { en: 'Thrall Damage', de: 'Begleiter-Schaden' },
    description: {
      en: 'Outgoing damage multiplier for thralls and minions.',
      de: 'Ausgehender Schadensmultiplikator fuer Begleiter und Thralls.',
    },
  },
  MinionDamageTakenMultiplier: {
    label: { en: 'Thrall Damage Taken', de: 'Begleiter-Schaden genommen' },
    description: {
      en: 'Incoming damage multiplier for thralls and minions.',
      de: 'Eingehender Schadensmultiplikator fuer Begleiter und Thralls.',
    },
  },
  StructureDamageMultiplier: {
    label: { en: 'Structure Damage', de: 'Strukturschaden' },
    description: {
      en: 'Outgoing damage multiplier against structures.',
      de: 'Schadensmultiplikator gegen Strukturen.',
    },
  },
  StructureDamageTakenMultiplier: {
    label: { en: 'Structure Damage Taken', de: 'Struktur-Schaden genommen' },
    description: {
      en: 'Incoming damage multiplier for structures.',
      de: 'Eingehender Schadensmultiplikator fuer Strukturen.',
    },
  },
  StructureHealthMultiplier: {
    label: { en: 'Structure Health', de: 'Struktur-Leben' },
    description: {
      en: 'Health multiplier for player-built structures.',
      de: 'Lebenspunkte-Multiplikator fuer Spieler-Bauten.',
    },
  },
  CanDamagePlayerOwnedStructures: {
    label: { en: 'Damage Player Structures', de: 'Spieler-Bauten beschaedigen' },
    description: {
      en: 'Allows player-owned structures to take damage.',
      de: 'Erlaubt Schaden an spielereigenen Strukturen.',
    },
  },
  BuildingPreloadRadius: {
    label: { en: 'Building Preload Radius', de: 'Bauwerk-Vorladeradius' },
    description: {
      en: 'Radius used to preload buildings around players.',
      de: 'Radius, in dem Bauwerke um Spieler vorgeladen werden.',
    },
  },
  ServerVoiceChat: {
    label: { en: 'Voice Chat', de: 'Voice-Chat' },
    description: {
      en: 'Enables server-side voice chat support.',
      de: 'Aktiviert serverseitige Voice-Chat-Unterstuetzung.',
    },
  },
}
