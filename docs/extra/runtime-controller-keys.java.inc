// Consume controller keys already delivered to gamepad state. Preserve volume.
{
    int keyCode = event.getKeyCode();
    if (keyCode != KeyEvent.KEYCODE_VOLUME_UP && keyCode != KeyEvent.KEYCODE_VOLUME_DOWN)
    {
        int src = event.getSource();
        if (((src & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD) ||
            ((src & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK))
        {
            return true;
        }
    }
}
