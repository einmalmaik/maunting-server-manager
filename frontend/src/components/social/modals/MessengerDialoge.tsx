import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'

import type { ChatGroupItem } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import { BlockConfirmDialog } from '@/components/social/modals/BlockConfirmDialog'
import { CameraSnapshotModal } from '@/components/social/CameraSnapshotModal'
import { ChatMuteDialog } from '@/components/social/modals/ChatMuteDialog'
import { CreateGroupDialog } from '@/components/social/modals/CreateGroupDialog'
import { DeleteGroupDialog } from '@/components/social/modals/DeleteGroupDialog'
import { FunkenRettungDialog } from '@/components/social/modals/FunkenRettungDialog'
import { GroupPermissionsModal } from '@/components/social/GroupPermissionsModal'
import { SafetyNumberDialog } from '@/components/social/modals/SafetyNumberDialog'
import { ChatHintergrundDialog } from '@/features/chatHintergrund'

interface Sicherheitsnummern {
  offen: boolean
  setOffen: (offen: boolean) => void
  geraete: any[]
  laedt: boolean
}

interface FunkenRettung {
  partnerId: number
  name: string
  anzahl: number
}

interface Props {
  t?: never
  isCreateGroupOpen: boolean
  setIsCreateGroupOpen: Dispatch<SetStateAction<boolean>>
  onCreateGroup: (name: string, beschreibung: string | null) => Promise<boolean>

  funkenRettung: FunkenRettung | null
  setFunkenRettung: Dispatch<SetStateAction<FunkenRettung | null>>
  onFunkenRettungBestaetigen: () => Promise<void>
  activeContact: ChatContact | null

  isCameraModalOpen: boolean
  setIsCameraModalOpen: (offen: boolean) => void
  augenblickModus: boolean
  setAugenblickModus: (offen: boolean) => void
  onCameraCapture: (dataUrl: string) => void

  isGroupPermissionsOpen: boolean
  setIsGroupPermissionsOpen: (offen: boolean) => void
  activeGroup: ChatGroupItem | null
  currentUserId: number
  onGroupUpdated: (gruppe: ChatGroupItem) => void

  groupToDelete: ChatGroupItem | null
  setGroupToDelete: (gruppe: ChatGroupItem | null) => void
  isDeletingGroup: boolean
  onConfirmDeleteGroup: () => Promise<void>

  isWallpaperModalOpen: boolean
  setIsWallpaperModalOpen: (offen: boolean) => void

  isMuteModalOpen: boolean
  setIsMuteModalOpen: (offen: boolean) => void
  blindMailboxId: string
  chatName: string | null

  sicherheitsnummern: Sicherheitsnummern
  isBlockConfirmOpen: boolean
  setIsBlockConfirmOpen: (offen: boolean) => void
  blocked: boolean
  onBlockConfirm: () => Promise<void>
}

/**
 * Die Dialoge der Messenger-Seite. Rein deklarativ: Sichtbarkeit und
 * Aktionen kommen als Props, die Entscheidungen bleiben in der Seite.
 */
export function MessengerDialoge({
  isCreateGroupOpen, setIsCreateGroupOpen, onCreateGroup,
  funkenRettung, setFunkenRettung, onFunkenRettungBestaetigen, activeContact,
  isCameraModalOpen, setIsCameraModalOpen, augenblickModus, setAugenblickModus,
  onCameraCapture, isGroupPermissionsOpen, setIsGroupPermissionsOpen, activeGroup,
  currentUserId, onGroupUpdated, groupToDelete, setGroupToDelete, isDeletingGroup,
  onConfirmDeleteGroup, isWallpaperModalOpen, setIsWallpaperModalOpen,
  isMuteModalOpen, setIsMuteModalOpen, blindMailboxId, chatName,
  sicherheitsnummern, isBlockConfirmOpen, setIsBlockConfirmOpen, blocked, onBlockConfirm,
}: Props) {
  const { t } = useTranslation()

  return (
    <>
      <CreateGroupDialog
        open={isCreateGroupOpen}
        onOpenChange={setIsCreateGroupOpen}
        onCreate={onCreateGroup}
      />

      <FunkenRettungDialog
        open={funkenRettung !== null}
        onOpenChange={(offen) => { if (!offen) setFunkenRettung(null) }}
        contactName={funkenRettung?.name ?? ''}
        anzahl={funkenRettung?.anzahl ?? 0}
        onConfirm={onFunkenRettungBestaetigen}
      />

      <CameraSnapshotModal
        open={isCameraModalOpen}
        onOpenChange={(offen) => {
          setIsCameraModalOpen(offen)
          if (!offen) setAugenblickModus(false)
        }}
        titel={augenblickModus ? t('messenger.moment.take') : undefined}
        bestaetigen={augenblickModus ? t('messenger.moment.send') : undefined}
        nurKamera={augenblickModus}
        mitStempel={augenblickModus}
        onCapture={onCameraCapture}
      />

      <GroupPermissionsModal
        open={isGroupPermissionsOpen}
        onOpenChange={setIsGroupPermissionsOpen}
        group={activeGroup}
        currentUserId={currentUserId || 0}
        onGroupUpdated={onGroupUpdated}
      />

      <DeleteGroupDialog
        group={groupToDelete}
        deleting={isDeletingGroup}
        onCancel={() => setGroupToDelete(null)}
        onConfirm={onConfirmDeleteGroup}
      />

      <ChatHintergrundDialog
        bereich="messenger"
        offen={isWallpaperModalOpen}
        onOffenChange={setIsWallpaperModalOpen}
      />

      <ChatMuteDialog
        open={isMuteModalOpen}
        onOpenChange={setIsMuteModalOpen}
        mailboxId={blindMailboxId || null}
        chatName={chatName}
      />

      <SafetyNumberDialog
        open={sicherheitsnummern.offen}
        onOpenChange={sicherheitsnummern.setOffen}
        contactName={activeContact?.username || ''}
        devices={sicherheitsnummern.geraete}
        loading={sicherheitsnummern.laedt}
      />

      <BlockConfirmDialog
        open={isBlockConfirmOpen}
        onOpenChange={setIsBlockConfirmOpen}
        contactName={activeContact?.username ?? ''}
        blocked={blocked}
        onConfirm={onBlockConfirm}
      />
    </>
  )
}
