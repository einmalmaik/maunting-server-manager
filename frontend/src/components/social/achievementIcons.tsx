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
  User,
  Fingerprint,
  FileText,
  Moon,
  Keyboard,
  Key,
  Volume2,
  Sliders,
  Mic,
  CheckCircle,
  Compass,
  RefreshCw,
  Network,
  FileCode,
  Package,
  Folder,
  UploadCloud,
  Activity,
  HardDrive,
  Shield,
  Zap,
  Users,
  Bookmark,
  Wrench,
  Calendar,
  RotateCcw,
  Cloud,
  Database,
  Trash2,
  Eye,
  UserCheck,
  LifeBuoy,
  Brain,
  Globe,
  XCircle,
  Paperclip,
  MapPin,
  Camera,
  MessageCircle,
  Share2,
  Smile,
  Crown,
  Briefcase,
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
    case 'user':
      return <User className={className} />
    case 'fingerprint':
      return <Fingerprint className={className} />
    case 'file-text':
      return <FileText className={className} />
    case 'moon':
      return <Moon className={className} />
    case 'keyboard':
      return <Keyboard className={className} />
    case 'key':
      return <Key className={className} />
    case 'volume-2':
      return <Volume2 className={className} />
    case 'sliders':
      return <Sliders className={className} />
    case 'mic':
      return <Mic className={className} />
    case 'check-circle':
      return <CheckCircle className={className} />
    case 'compass':
      return <Compass className={className} />
    case 'refresh-cw':
      return <RefreshCw className={className} />
    case 'network':
      return <Network className={className} />
    case 'file-code':
      return <FileCode className={className} />
    case 'package':
      return <Package className={className} />
    case 'folder':
      return <Folder className={className} />
    case 'upload-cloud':
      return <UploadCloud className={className} />
    case 'activity':
      return <Activity className={className} />
    case 'hard-drive':
      return <HardDrive className={className} />
    case 'shield':
      return <Shield className={className} />
    case 'zap':
      return <Zap className={className} />
    case 'users':
      return <Users className={className} />
    case 'bookmark':
      return <Bookmark className={className} />
    case 'tool':
      return <Wrench className={className} />
    case 'calendar':
      return <Calendar className={className} />
    case 'rotate-ccw':
      return <RotateCcw className={className} />
    case 'cloud':
      return <Cloud className={className} />
    case 'database':
      return <Database className={className} />
    case 'trash-2':
      return <Trash2 className={className} />
    case 'eye':
      return <Eye className={className} />
    case 'user-check':
      return <UserCheck className={className} />
    case 'life-buoy':
      return <LifeBuoy className={className} />
    case 'brain':
      return <Brain className={className} />
    case 'globe':
      return <Globe className={className} />
    case 'x-circle':
      return <XCircle className={className} />
    case 'paperclip':
      return <Paperclip className={className} />
    case 'map-pin':
      return <MapPin className={className} />
    case 'camera':
      return <Camera className={className} />
    case 'message-circle':
      return <MessageCircle className={className} />
    case 'share-2':
      return <Share2 className={className} />
    case 'smile':
      return <Smile className={className} />
    case 'crown':
      return <Crown className={className} />
    case 'briefcase':
      return <Briefcase className={className} />
    case 'trophy':
    default:
      return <Trophy className={className} />
  }
}
