import { Button, Checkbox, Group, List, Modal, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { acceptTerms } from '../api/auth'
import { useAuth } from '../auth/AuthContext'

// If the points below change in a way people should agree to again, bump
// TERMS_VERSION in game_sheet_core.py: everyone is then asked once more.

/** The user agreement: shown the first time someone uses the app, over
 * whatever page they opened, until they agree or sign out. */
export function TermsGate() {
  const { user, logout, refreshUser } = useAuth()
  const [checked, setChecked] = useState(false)
  const agreeMutation = useMutation({
    mutationFn: acceptTerms,
    onSuccess: refreshUser,
    onError: () => notifications.show({ color: 'red', message: "Couldn't save that. Try again in a moment." }),
  })

  if (!user || user.terms_accepted) return null

  return (
    <Modal
      opened
      onClose={() => {}}
      withCloseButton={false}
      closeOnClickOutside={false}
      closeOnEscape={false}
      centered
      size="lg"
      title="Before you start: how this site treats your information"
    >
      <Stack ta="left">
        <Text size="sm">
          Team Pittsburgh Team Manager is run by volunteers for Team Pittsburgh Ball Hockey. Please read and agree to
          the following to use it.
        </Text>
        <List spacing="sm" size="sm">
          <List.Item>
            <b>This is an open-source website.</b> The software that runs it is published openly, so anyone can see how
            it works. The information stored in it (players, families, contact details) is not public and is only
            visible to people signed in.
          </List.Item>
          <List.Item>
            <b>You agree to be emailed about administrative matters.</b> That means things like your account, team
            assignments, schedules and league announcements. You won&apos;t be sent advertising.
          </List.Item>
          <List.Item>
            <b>Your information is not sold, shared or reported to anyone.</b> It is used only to run the league:
            organizing teams, schedules, games and stats.
          </List.Item>
          <List.Item>
            <b>It never will be.</b> Your information will not be sold or used for any other purpose in the future.
          </List.Item>
        </List>
        <Checkbox
          label="I have read this and I agree."
          checked={checked}
          onChange={(e) => setChecked(e.currentTarget.checked)}
        />
        <Group justify="space-between">
          <Button variant="subtle" color="gray" onClick={logout}>
            Sign out instead
          </Button>
          <Button disabled={!checked} loading={agreeMutation.isPending} onClick={() => agreeMutation.mutate()}>
            I agree
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
