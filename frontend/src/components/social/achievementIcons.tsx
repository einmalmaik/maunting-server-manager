import React from 'react'
import {
  Trophy,
  Award,
  ShieldCheck,
  Server,
  Layers,
  Archive,
  Terminal,
  Sparkles,
  Bot,
  Cpu,
  UserPlus,
  Lock,
  Clock,
  Timer,
  Flame,
} from 'lucide-react'

export function renderAchievementIcon(iconName: string, className = 'w-6 h-6'): React.ReactNode {
  switch (iconName) {
    case 'award':
      return <Award className={className} />
    case 'shield-check':
      return <ShieldCheck className={className} />
    case 'server':
      return <Server className={className} />
    case 'layers':
      return <Layers className={className} />
    case 'archive':
      return <Archive className={className} />
    case 'terminal':
      return <Terminal className={className} />
    case 'sparkles':
      return <Sparkles className={className} />
    case 'bot':
      return <Bot className={className} />
    case 'cpu':
      return <Cpu className={className} />
    case 'user-plus':
      return <UserPlus className={className} />
    case 'lock':
      return <Lock className={className} />
    case 'clock':
      return <Clock className={className} />
    case 'timer':
      return <Timer className={className} />
    case 'flame':
      return <Flame className={className} />
    case 'trophy':
    default:
      return <Trophy className={className} />
  }
}
